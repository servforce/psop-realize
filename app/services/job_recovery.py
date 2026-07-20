from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import CallLog, Standard, StandardMaterializeJob, VideoJob, WireframeJob


INTERRUPTED_JOB_MESSAGE = "服务中断，后台任务未完成，请重新发起解析。"


def fail_interrupted_background_jobs(session: Session) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    counts = {
        "standard_materialize_jobs": _fail_standard_materialize_jobs(session, now),
        "standards": _fail_processing_standards(session, now),
        "video_jobs": _fail_video_jobs(session, now),
        "wireframe_jobs": _fail_wireframe_jobs(session, now),
        "call_logs": _fail_call_logs(session),
    }
    session.commit()
    return counts


def _fail_standard_materialize_jobs(session: Session, now: datetime) -> int:
    jobs = session.scalars(
        select(StandardMaterializeJob).where(StandardMaterializeJob.status == "running")
    ).all()
    for job in jobs:
        job.status = "failed"
        job.stage = "failed"
        job.progress_percent = 100
        job.message = INTERRUPTED_JOB_MESSAGE
        job.error_message = INTERRUPTED_JOB_MESSAGE
        job.completed_at = now
        job.updated_at = now
        session.add(job)
    return len(jobs)


def _fail_processing_standards(session: Session, now: datetime) -> int:
    standards = session.scalars(select(Standard).where(Standard.status == "processing")).all()
    for standard in standards:
        standard.status = "failed"
        standard.updated_at = now
        session.add(standard)
    return len(standards)


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
