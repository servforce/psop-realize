from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.standard_library import StandardLibrarySessionLocal
from app.models.entities import StandardProcessingJob
from app.models.standard_library import CallLog, VideoJob, WireframeJob


INTERRUPTED_JOB_MESSAGE = "鏈嶅姟涓柇锛屽悗鍙颁换鍔℃湭瀹屾垚锛岃閲嶆柊鍙戣捣瑙ｆ瀽銆?"


def fail_interrupted_background_jobs(session: Session) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    counts = {
        "standard_processing_jobs": _fail_standard_processing_jobs(session, now),
        "video_jobs": 0,
        "wireframe_jobs": 0,
        "call_logs": 0,
    }
    with StandardLibrarySessionLocal() as library_session:
        counts["video_jobs"] = _fail_video_jobs(library_session, now)
        counts["wireframe_jobs"] = _fail_wireframe_jobs(library_session, now)
        counts["call_logs"] = _fail_call_logs(library_session)
        library_session.commit()
    session.commit()
    return counts


def _fail_standard_processing_jobs(session: Session, now: datetime) -> int:
    jobs = session.scalars(select(StandardProcessingJob).where(StandardProcessingJob.status == "running")).all()
    for job in jobs:
        job.status = "failed"
        job.stage = "failed"
        job.progress_percent = 100
        job.error_message = INTERRUPTED_JOB_MESSAGE
        job.completed_at = now
        job.updated_at = now
        session.add(job)
    return len(jobs)


def _fail_video_jobs(session: Session, now: datetime) -> int:
    jobs = session.scalars(select(VideoJob).where(VideoJob.status == "processing")).all()
    for job in jobs:
        job.status = "failed"
        job.current_stage = "failed"
        job.progress_percent = 100
        job.error_message = INTERRUPTED_JOB_MESSAGE
        job.completed_at = now
        job.updated_at = now
        session.add(job)
    return len(jobs)


def _fail_wireframe_jobs(session: Session, now: datetime) -> int:
    jobs = session.scalars(
        select(WireframeJob).where(WireframeJob.status.in_(("queued", "processing")))
    ).all()
    for job in jobs:
        job.status = "failed"
        job.progress_percent = 100
        job.error_message = INTERRUPTED_JOB_MESSAGE
        job.completed_at = now
        job.updated_at = now
        session.add(job)
    return len(jobs)


def _fail_call_logs(session: Session) -> int:
    logs = session.scalars(select(CallLog).where(CallLog.status == "running")).all()
    for log in logs:
        log.status = "failed"
        log.error_message = INTERRUPTED_JOB_MESSAGE
        session.add(log)
    return len(logs)
