from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.video import VideoSessionLocal
from app.models.video import VideoJob


INTERRUPTED_JOB_MESSAGE = "Background job was interrupted by service restart."


def fail_interrupted_video_jobs() -> dict[str, int]:
    now = datetime.now(timezone.utc)
    counts = {"video_jobs": 0}
    with VideoSessionLocal() as session:
        counts["video_jobs"] = _fail_video_jobs(session, now)
        session.commit()
    return counts


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
