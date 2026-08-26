from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.db.video import VideoSessionLocal
from app.models.video import VideoUsageRecord


router = APIRouter(prefix="/api/usage", tags=["usage"])

FEATURE_NAMES = ("转写文本", "筛选业务帧", "生成 Markdown")


@router.get("/summary")
def get_usage_summary():
    with VideoSessionLocal() as session:
        records = list(
            session.scalars(
                select(VideoUsageRecord)
                .where(
                    VideoUsageRecord.interface_type == "background",
                    VideoUsageRecord.tool_or_endpoint == "video_parse_job",
                    VideoUsageRecord.feature_name.in_(FEATURE_NAMES),
                )
                .order_by(VideoUsageRecord.created_at.desc())
            )
        )
    return {"items": [usage_record_row(record) for record in records]}


def usage_record_row(record: VideoUsageRecord) -> dict:
    return {
        "id": record.id,
        "video_id": record.video_id,
        "feature_name": record.feature_name,
        "model": record.model or "本地流程",
        "models": record.model or "本地流程",
        "prompt_tokens": int(record.prompt_tokens or 0),
        "completion_tokens": int(record.completion_tokens or 0),
        "total_tokens": int(record.total_tokens or 0),
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        "status": record.status,
        "status_label": status_label(record.status),
    }


def status_label(status: str | None) -> str:
    if status == "processing":
        return "进行中"
    if status == "failed":
        return "失败"
    return "已完成"
