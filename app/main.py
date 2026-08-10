from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api import config, logs, standards, videos, wireframes
from app.core.config import settings
from app.jobs.standard_update_scheduler import StandardUpdateScheduler
from app.db.session import SessionLocal, init_db
from app.services.job_recovery import fail_interrupted_background_jobs
from app.services.storage import storage_service


logger = logging.getLogger(__name__)
logging.getLogger("app").setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with SessionLocal() as session:
        counts = fail_interrupted_background_jobs(session)
    if any(counts.values()):
        logger.warning("marked interrupted background jobs as failed: %s", counts)
    scheduler = None
    scheduler_task = None
    if settings.standard_update_scheduler_enabled:
        scheduler = StandardUpdateScheduler()
        scheduler_task = asyncio.create_task(scheduler.run_forever(), name="standard-update-scheduler")
        app.state.standard_update_scheduler = scheduler
        app.state.standard_update_scheduler_task = scheduler_task
        logger.info("standard update scheduler enabled")
        print("standard update scheduler enabled", flush=True)
    else:
        logger.info("standard update scheduler disabled")
        print("standard update scheduler disabled", flush=True)
    yield
    if scheduler is not None:
        scheduler.stop()
    if scheduler_task is not None:
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("standard update scheduler stopped with error")


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(title="Servforce Material Workbench", version="0.1.0", lifespan=lifespan)
    app.include_router(videos.router)
    app.include_router(wireframes.router)
    app.include_router(standards.router)
    app.include_router(config.router)
    app.include_router(logs.router)
    app.mount("/static", StaticFiles(directory="static"), name="static")

    @app.get("/")
    def index():
        return FileResponse(
            "static/index.html",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
            },
        )

    @app.get("/api/objects/{object_key:path}")
    def object_proxy(object_key: str):
        content = storage_service.get_bytes(bucket=settings.object_store_bucket, object_key=object_key)
        media_type = "application/octet-stream"
        if object_key.endswith(".jpg"):
            media_type = "image/jpeg"
        elif object_key.endswith(".svg"):
            media_type = "image/svg+xml"
        elif object_key.endswith(".png"):
            media_type = "image/png"
        elif object_key.endswith(".md"):
            media_type = "text/markdown; charset=utf-8"
        elif object_key.endswith(".pdf"):
            media_type = "application/pdf"
        return Response(content=content, media_type=media_type)

    @app.get("/health")
    def health():
        return {"ok": True, "app": "servforce-material-workbench"}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8090)
