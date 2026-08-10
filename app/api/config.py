from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config")
def get_runtime_config():
    return {
        "standard_update_scheduler_enabled": settings.standard_update_scheduler_enabled,
    }
