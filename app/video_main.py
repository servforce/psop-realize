from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api import semantic_frames, usage, videos, wireframes
from app.core.video_config import video_settings as settings
from app.db.video import init_video_db
from app.services.storage import storage_service
from app.services.video_job_recovery import fail_interrupted_video_jobs


logger = logging.getLogger(__name__)
logging.getLogger("app").setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_video_db()
    counts = fail_interrupted_video_jobs()
    if any(counts.values()):
        logger.warning("marked interrupted video jobs as failed: %s", counts)
    yield


def create_app() -> FastAPI:
    init_video_db()
    app = FastAPI(title="Servforce Video Service", version="0.1.0", lifespan=lifespan)
    app.include_router(videos.router)
    app.include_router(wireframes.router)
    app.include_router(semantic_frames.router)
    app.include_router(usage.router)
    app.mount("/static", StaticFiles(directory="static"), name="static")

    @app.get("/")
    def index():
        return FileResponse(
            "static/video.html",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
            },
        )

    @app.get("/api/objects/{object_key:path}")
    def object_proxy(object_key: str):
        content = storage_service.get_bytes(bucket=settings.object_store_bucket, object_key=object_key)
        return Response(content=content, media_type=media_type_for_object_key(object_key))

    @app.get("/health")
    def health():
        return {"ok": True, "app": "servforce-video-service"}

    return app


def media_type_for_object_key(object_key: str) -> str:
    if object_key.endswith(".jpg") or object_key.endswith(".jpeg"):
        return "image/jpeg"
    if object_key.endswith(".svg"):
        return "image/svg+xml"
    if object_key.endswith(".png"):
        return "image/png"
    if object_key.endswith(".md"):
        return "text/markdown; charset=utf-8"
    if object_key.endswith(".pdf"):
        return "application/pdf"
    if object_key.endswith(".mp4"):
        return "video/mp4"
    return "application/octet-stream"


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.video_main:app", host="127.0.0.1", port=8090)
