from __future__ import annotations

import traceback
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import settings
from app.db.standard_library import StandardLibrarySessionLocal
from app.models.standard_library import VideoFrame, WireframeJob
from app.services.frame_selection import ensure_frame_selection
from app.services.wireframes import wireframe_service


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def create_wireframe_job(video_id: str) -> WireframeJob:
    with StandardLibrarySessionLocal() as session:
        frames = list(
            session.scalars(
                select(VideoFrame).where(VideoFrame.video_id == video_id).order_by(VideoFrame.timestamp_ms)
            )
        )
        job = WireframeJob(
            id=uuid.uuid4().hex,
            video_id=video_id,
            status="queued",
            progress_percent=0,
            total_frames=len(frames),
            completed_frames=0,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job


def run_wireframe_job(job_id: str) -> None:
    with StandardLibrarySessionLocal() as session:
        job = session.get(WireframeJob, job_id)
        if job is None:
            return
        try:
            frames = ensure_frame_selection(session, job.video_id)
            if not frames:
                fail_job(
                    session,
                    job,
                    "No selected frames are available for wireframe generation. Please parse keyframes and run frame selection first.",
                )
                return

            job.status = "processing"
            job.progress_percent = 3
            job.total_frames = len(frames)
            job.completed_frames = 0
            job.current_frame_id = None
            job.updated_at = now_utc()
            session.add(job)
            session.commit()

            if not settings.video_wireframe_generation_enabled:
                job.completed_frames = len(frames)
                job.progress_percent = 99
                job.current_frame_id = None
                job.updated_at = now_utc()
                session.add(job)
                session.commit()
                complete_job(session, job_id)
                return

            for index, frame in enumerate(frames, start=1):
                job = session.get(WireframeJob, job_id)
                if job is None:
                    return
                job.current_frame_id = frame.id
                job.updated_at = now_utc()
                session.add(job)
                session.commit()

                wireframe_service.generate_wireframe(session, frame.id)

                job = session.get(WireframeJob, job_id)
                if job is None:
                    return
                job.completed_frames = index
                job.progress_percent = min(99, int(round(index / len(frames) * 100)))
                job.updated_at = now_utc()
                session.add(job)
                session.commit()

            complete_job(session, job_id)
        except Exception as exc:
            job = session.get(WireframeJob, job_id)
            if job is not None:
                job.status = "failed"
                job.progress_percent = 100
                job.error_message = f"{exc}\n{traceback.format_exc(limit=4)}"
                job.completed_at = now_utc()
                job.updated_at = now_utc()
                session.add(job)
                session.commit()


def fail_job(session, job: WireframeJob, message: str) -> None:
    job.status = "failed"
    job.progress_percent = 100
    job.error_message = message
    job.completed_at = now_utc()
    job.updated_at = now_utc()
    session.add(job)
    session.commit()


def complete_job(session, job_id: str) -> None:
    job = session.get(WireframeJob, job_id)
    if job is None:
        return
    job.status = "completed"
    job.progress_percent = 100
    job.current_frame_id = None
    job.completed_at = now_utc()
    job.updated_at = now_utc()
    session.add(job)
    session.commit()


def latest_wireframe_job(video_id: str) -> WireframeJob | None:
    with StandardLibrarySessionLocal() as session:
        job = session.scalars(
            select(WireframeJob).where(WireframeJob.video_id == video_id).order_by(WireframeJob.created_at.desc())
        ).first()
        if job is not None:
            session.expunge(job)
        return job


def get_wireframe_job(job_id: str) -> WireframeJob | None:
    with StandardLibrarySessionLocal() as session:
        job = session.get(WireframeJob, job_id)
        if job is not None:
            session.expunge(job)
        return job


def wireframe_job_to_dict(job: WireframeJob) -> dict:
    return {
        "id": job.id,
        "video_id": job.video_id,
        "status": job.status,
        "progress_percent": job.progress_percent,
        "total_frames": job.total_frames,
        "completed_frames": job.completed_frames,
        "current_frame_id": job.current_frame_id,
        "error_message": job.error_message or "",
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }
