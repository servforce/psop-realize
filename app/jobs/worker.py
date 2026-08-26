from __future__ import annotations

import asyncio
import logging

from app.core.video_config import VideoSettings, video_settings
from app.db.video import VideoSessionLocal
from app.services.video_repository import VideoJobRepository
from app.services.videos import VideoJobRunner


logger = logging.getLogger(__name__)


class VideoWorker:
    def __init__(self, settings_: VideoSettings = video_settings) -> None:
        self.settings = settings_
        self.runner = VideoJobRunner()
        self._stop = asyncio.Event()

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            job_id = await asyncio.to_thread(self._claim_next_job_id)
            if not job_id:
                await asyncio.sleep(self.settings.worker_poll_interval_seconds)
                continue
            try:
                await asyncio.to_thread(self.runner.run, job_id)
            except Exception:
                logger.exception("video job failed: %s", job_id)

    def stop(self) -> None:
        self._stop.set()

    def _claim_next_job_id(self) -> str | None:
        with VideoSessionLocal() as session:
            repo = VideoJobRepository(session)
            job = repo.claim_next_job()
            return job.id if job else None
