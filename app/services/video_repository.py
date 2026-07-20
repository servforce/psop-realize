from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import VideoFrame, VideoJob


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class VideoJobRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_job(
        self,
        *,
        video_id: str,
        title: str,
        filename: str,
        content_type: str,
        size_bytes: int,
        source_bucket: str,
        source_object_key: str,
    ) -> VideoJob:
        job = VideoJob(
            id=video_id,
            title=title,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            source_bucket=source_bucket,
            source_object_key=source_object_key,
            status="uploaded",
            progress_percent=0,
            current_stage="uploaded",
        )
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job

    def list_jobs(self, *, limit: int = 30) -> list[VideoJob]:
        statement = select(VideoJob).order_by(VideoJob.created_at.desc()).limit(limit)
        return list(self.session.scalars(statement).all())

    def get_job(self, video_id: str) -> VideoJob | None:
        return self.session.get(VideoJob, video_id)

    def claim_next_job(self) -> VideoJob | None:
        statement = (
            select(VideoJob)
            .where(VideoJob.status == "queued")
            .order_by(VideoJob.created_at.asc())
            .limit(1)
        )
        job = self.session.scalars(statement).first()
        if job is None:
            return None
        job.status = "processing"
        job.current_stage = "downloading_source"
        job.progress_percent = max(job.progress_percent, 10)
        job.updated_at = now_utc()
        self.session.commit()
        self.session.refresh(job)
        return job

    def update_job(
        self,
        video_id: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        progress: int | None = None,
        error_message: str | None = None,
        duration_ms: int | None = None,
        frame_count: int | None = None,
        audio_temp_path: str | None = None,
        transcript_object_key: str | None = None,
        markdown_object_key: str | None = None,
        completed: bool = False,
    ) -> VideoJob:
        job = self.session.get(VideoJob, video_id)
        if job is None:
            raise KeyError(video_id)
        if status is not None:
            job.status = status
        if stage is not None:
            job.current_stage = stage
        if progress is not None:
            job.progress_percent = max(0, min(100, int(progress)))
        if error_message is not None:
            job.error_message = error_message
        if duration_ms is not None:
            job.duration_ms = int(duration_ms)
        if frame_count is not None:
            job.frame_count = int(frame_count)
        if audio_temp_path is not None:
            job.audio_temp_path = audio_temp_path
        if transcript_object_key is not None:
            job.transcript_object_key = transcript_object_key
        if markdown_object_key is not None:
            job.markdown_object_key = markdown_object_key
        if completed:
            job.completed_at = now_utc()
        job.updated_at = now_utc()
        self.session.commit()
        self.session.refresh(job)
        return job

    def replace_frames(self, video_id: str, frames: Iterable[VideoFrame]) -> None:
        for existing in self.session.scalars(select(VideoFrame).where(VideoFrame.video_id == video_id)):
            self.session.delete(existing)
        for frame in frames:
            self.session.add(frame)
        self.session.commit()

    def frames_for_video(self, video_id: str) -> list[VideoFrame]:
        statement = select(VideoFrame).where(VideoFrame.video_id == video_id).order_by(VideoFrame.timestamp_ms.asc())
        return list(self.session.scalars(statement).all())
