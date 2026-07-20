from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.entities import CallLog

router = APIRouter(prefix="/api", tags=["logs"])


@router.get("/call-logs")
def call_logs(limit: int = 100):
    with SessionLocal() as session:
        rows = session.scalars(select(CallLog).order_by(CallLog.created_at.desc()).limit(min(limit, 500))).all()
        return [call_log_to_dict(row) for row in rows]


def call_log_to_dict(row: CallLog) -> dict:
    return {
        "id": row.id,
        "interface_type": row.interface_type,
        "caller": row.caller,
        "tool_or_endpoint": row.tool_or_endpoint,
        "request_summary": row.request_summary,
        "response_summary": row.response_summary,
        "status": row.status,
        "error_message": row.error_message,
        "duration_ms": row.duration_ms,
        "video_id": row.video_id,
        "standard_id": row.standard_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
