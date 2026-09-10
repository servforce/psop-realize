from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import delete, select

from app.models.video import VideoFrame, VideoJob


class VideoDeletionConflict(Exception):
    pass


class VideoNotFoundError(Exception):
    pass


def delete_video_data(video_id: str, *, session_factory, storage, default_bucket: str) -> None:
    """Persist a retryable deletion marker before touching object storage.

    Parse admission takes the same row lock and rejects `deleting` jobs. If
    storage cleanup fails or the process stops, DELETE can safely be retried;
    never report success with orphaned current objects. Usage history is kept.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", video_id):
        raise ValueError("Invalid video ID")
    with session_factory() as session:
        job = session.scalar(select(VideoJob).where(VideoJob.id == video_id).with_for_update())
        if job is None:
            raise VideoNotFoundError(video_id)
        if job.status not in {"uploaded", "completed", "completed_with_warnings", "failed", "deleting"}:
            raise VideoDeletionConflict("上传或解析中的视频不能删除，请等待任务结束。")
        job.status = "deleting"
        job.current_stage = "deleting"
        job.updated_at = datetime.now(timezone.utc)
        session.commit()

    with session_factory() as session:
        # Serialize concurrent retries through completion of storage and DB cleanup.
        job = session.scalar(select(VideoJob).where(VideoJob.id == video_id).with_for_update())
        if job is None:
            return
        buckets = {default_bucket, job.source_bucket, job.analysis_video_bucket}
        buckets.update(session.scalars(select(VideoFrame.bucket).where(VideoFrame.video_id == video_id)))
        for bucket in sorted(b for b in buckets if b):
            storage.delete_video_objects(bucket=bucket, video_id=video_id)
        session.execute(delete(VideoFrame).where(VideoFrame.video_id == video_id))
        session.delete(job)
        session.commit()
