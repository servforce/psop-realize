from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from functools import partial
from inspect import signature

import anyio
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import videos
from app.db.video import VideoBase
from app.models.video import VideoJob
from app.services.storage import StoredObject


CLIENT_TASK_ID = "123e4567e89b42d3a456426614174000"


@pytest.fixture
def video_api(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VideoBase.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    monkeypatch.setattr(videos, "VideoSessionLocal", session_factory)
    monkeypatch.setattr(videos, "settings", replace(videos.settings, video_workdir=str(tmp_path)))

    def fake_upload_file(*, object_key, path, media_type, bucket=None):
        return StoredObject(
            bucket=bucket or "test-bucket",
            object_key=object_key,
            media_type=media_type,
            size_bytes=path.stat().st_size,
            checksum="test-checksum",
        )

    monkeypatch.setattr(videos.storage_service, "upload_file", fake_upload_file)

    async def run_inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(videos, "run_in_threadpool", run_inline)

    app = FastAPI()
    app.include_router(videos.router)
    yield app, session_factory

    engine.dispose()


@pytest.fixture
def anyio_backend():
    return "asyncio"


def complete_job(session_factory, video_id: str) -> None:
    with session_factory() as session:
        job = session.get(VideoJob, video_id)
        assert job is not None
        job.status = "completed"
        job.current_stage = "completed"
        job.progress_percent = 100
        session.add(job)
        session.commit()


@pytest.mark.anyio
async def test_upload_and_parse_waits_for_full_parse_and_returns_completed(video_api, monkeypatch):
    app, session_factory = video_api
    call_order: list[str] = []

    def fake_upload_file(*, object_key, path, media_type, bucket=None):
        call_order.append("upload")
        assert path.read_bytes() == b"video-content"
        return StoredObject(
            bucket="test-bucket",
            object_key=object_key,
            media_type=media_type,
            size_bytes=path.stat().st_size,
            checksum="test-checksum",
        )

    def fake_parse_full(video_id: str):
        call_order.append("parse")
        with session_factory() as session:
            job = session.get(VideoJob, video_id)
            assert job is not None
            assert job.status == "processing"
            assert job.current_stage == "transcribing_asr"
            assert job.progress_percent == 5
        complete_job(session_factory, video_id)

    monkeypatch.setattr(videos.storage_service, "upload_file", fake_upload_file)
    monkeypatch.setattr(videos, "parse_full", fake_parse_full)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/videos/upload-and-parse",
            files={"file": ("demo.mp4", b"video-content", "video/mp4")},
            data={"title": "演示视频", "task_id": CLIENT_TASK_ID},
        )

    assert response.status_code == 200
    assert call_order == ["upload", "parse"]
    payload = response.json()
    assert payload["video_id"] == CLIENT_TASK_ID
    assert payload["job"]["id"] == CLIENT_TASK_ID
    assert payload["success"] is True
    assert payload["status"] == "completed"
    assert payload["job"]["status"] == "completed"
    assert payload["job"]["progress_percent"] == 100
    assert payload["job"]["title"] == "演示视频"
    assert payload["error"] is None


@pytest.mark.anyio
async def test_upload_and_parse_does_not_respond_before_parser_finishes(video_api, monkeypatch):
    app, session_factory = video_api
    parse_started = asyncio.Event()
    allow_parse_to_finish = asyncio.Event()

    def fake_parse_full(video_id: str):
        complete_job(session_factory, video_id)

    monkeypatch.setattr(videos, "parse_full", fake_parse_full)

    async def controlled_run_in_threadpool(function, *args, **kwargs):
        if function is videos.run_video_parse_job:
            parse_started.set()
            await allow_parse_to_finish.wait()
        return function(*args, **kwargs)

    monkeypatch.setattr(videos, "run_in_threadpool", controlled_run_in_threadpool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        request = asyncio.create_task(
            client.post(
                "/api/videos/upload-and-parse",
                files={"file": ("demo.mp4", b"video-content", "video/mp4")},
                data={"task_id": CLIENT_TASK_ID},
            )
        )
        await asyncio.wait_for(parse_started.wait(), timeout=2)
        running_job = videos.get_video(CLIENT_TASK_ID)
        assert running_job["id"] == CLIENT_TASK_ID
        assert running_job["status"] == "processing"
        assert running_job["current_stage"] == "transcribing_asr"
        assert running_job["progress_percent"] == 5
        await asyncio.sleep(0.05)
        assert request.done() is False
        allow_parse_to_finish.set()
        response = await asyncio.wait_for(request, timeout=3)

    assert response.status_code == 200
    assert response.json()["status"] == "completed"


@pytest.mark.anyio
async def test_upload_and_parse_returns_stable_failed_response(video_api, monkeypatch):
    app, session_factory = video_api

    def fake_parse_full(video_id: str):
        with session_factory() as session:
            job = session.get(VideoJob, video_id)
            assert job is not None
            job.status = "failed"
            job.current_stage = "failed"
            job.progress_percent = 100
            job.error_message = "模型推理失败\nTraceback: internal details"
            session.add(job)
            session.commit()
        raise RuntimeError("模型推理失败")

    monkeypatch.setattr(videos, "parse_full", fake_parse_full)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/videos/upload-and-parse",
            files={"file": ("demo.mp4", b"video-content", "video/mp4")},
        )
    status_payload = videos.get_video(response.json()["video_id"])

    assert response.status_code == 500
    payload = response.json()
    assert payload["success"] is False
    assert payload["status"] == "failed"
    assert payload["error"] == {
        "code": "VIDEO_PARSE_FAILED",
        "message": "视频解析失败",
    }
    assert payload["job"]["error_message"] == "视频解析失败"
    assert "Traceback" not in response.text
    assert status_payload["error_message"] == "视频解析失败"
    assert "Traceback" not in str(status_payload)


@pytest.mark.anyio
async def test_unexpected_parser_error_persists_terminal_failed_status(video_api, monkeypatch):
    app, _ = video_api

    def failing_parse_without_status_update(video_id: str):
        raise RuntimeError(f"unexpected parser wrapper failure: {video_id}")

    monkeypatch.setattr(videos, "parse_full", failing_parse_without_status_update)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/videos/upload-and-parse",
            files={"file": ("demo.mp4", b"video-content", "video/mp4")},
            data={"task_id": CLIENT_TASK_ID},
        )
    status_payload = videos.get_video(CLIENT_TASK_ID)

    assert response.status_code == 500
    assert response.json()["status"] == "failed"
    assert response.json()["job"]["status"] == "failed"
    assert status_payload["status"] == "failed"
    assert status_payload["error_message"] == "视频解析失败"


@pytest.mark.anyio
async def test_parser_return_without_terminal_status_is_marked_failed(video_api, monkeypatch):
    app, _ = video_api

    monkeypatch.setattr(videos, "parse_full", lambda video_id: None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/videos/upload-and-parse",
            files={"file": ("demo.mp4", b"video-content", "video/mp4")},
            data={"task_id": CLIENT_TASK_ID},
        )
    status_payload = videos.get_video(CLIENT_TASK_ID)

    assert response.status_code == 500
    assert response.json()["status"] == "failed"
    assert status_payload["status"] == "failed"


@pytest.mark.anyio
async def test_upload_and_parse_rejects_invalid_video_before_parsing(video_api, monkeypatch):
    app, _ = video_api

    def unexpected_parse(video_id: str):
        pytest.fail(f"invalid upload must not be parsed: {video_id}")

    monkeypatch.setattr(videos, "parse_full", unexpected_parse)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/videos/upload-and-parse",
            files={"file": ("notes.txt", b"not-a-video", "text/plain")},
        )

    assert response.status_code == 400
    assert response.json()["detail"].startswith("不支持的视频格式")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "task_id",
    [
        "../not-safe",
        "123E4567E89B42D3A456426614174000",
        "123e4567e89b12d3a456426614174000",
    ],
)
async def test_upload_and_parse_rejects_invalid_client_task_id(video_api, monkeypatch, task_id):
    app, _ = video_api
    storage_called = False
    parser_called = False

    def unexpected_upload(*, object_key, path, media_type, bucket=None):
        nonlocal storage_called
        storage_called = True

    def unexpected_parse(video_id: str):
        nonlocal parser_called
        parser_called = True

    monkeypatch.setattr(videos.storage_service, "upload_file", unexpected_upload)
    monkeypatch.setattr(videos, "parse_full", unexpected_parse)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/videos/upload-and-parse",
            files={"file": ("demo.mp4", b"video-content", "video/mp4")},
            data={"task_id": task_id},
        )

    assert response.status_code == 400
    assert response.json()["detail"].startswith("task_id 必须是")
    assert storage_called is False
    assert parser_called is False


@pytest.mark.anyio
async def test_upload_and_parse_rejects_duplicate_client_task_id_before_storage(video_api, monkeypatch):
    app, session_factory = video_api
    storage_called = False
    parser_called = False
    with session_factory() as session:
        session.add(
            VideoJob(
                id=CLIENT_TASK_ID,
                title="existing",
                filename="existing.mp4",
                content_type="video/mp4",
                size_bytes=1,
                source_bucket="test-bucket",
                source_object_key="videos/existing/source.mp4",
                status="uploaded",
                current_stage="uploaded",
            )
        )
        session.commit()

    def unexpected_upload(*, object_key, path, media_type, bucket=None):
        nonlocal storage_called
        storage_called = True

    def unexpected_parse(video_id: str):
        nonlocal parser_called
        parser_called = True

    monkeypatch.setattr(videos.storage_service, "upload_file", unexpected_upload)
    monkeypatch.setattr(videos, "parse_full", unexpected_parse)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/videos/upload-and-parse",
            files={"file": ("demo.mp4", b"video-content", "video/mp4")},
            data={"task_id": CLIENT_TASK_ID},
        )

    assert response.status_code == 409
    assert "task_id 已存在" in response.json()["detail"]
    assert storage_called is False
    assert parser_called is False


@pytest.mark.anyio
async def test_concurrent_duplicate_task_ids_never_overwrite_source_object(monkeypatch, tmp_path):
    """Both racing uploads may reach storage, but they must never share an object key."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrent-task-id.db'}",
        connect_args={"check_same_thread": False},
    )
    VideoBase.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr(videos, "VideoSessionLocal", session_factory)
    monkeypatch.setattr(videos, "settings", replace(videos.settings, video_workdir=str(tmp_path / "work")))

    storage_barrier = threading.Barrier(2)
    storage_lock = threading.Lock()
    stored_contents: dict[str, bytes] = {}
    parse_calls: list[str] = []

    def racing_upload(*, object_key, path, media_type, bucket=None):
        with storage_lock:
            stored_contents[object_key] = path.read_bytes()
        storage_barrier.wait(timeout=3)
        return StoredObject(
            bucket=bucket or "test-bucket",
            object_key=object_key,
            media_type=media_type,
            size_bytes=path.stat().st_size,
            checksum="test-checksum",
        )

    async def run_in_worker(function, *args, **kwargs):
        return await anyio.to_thread.run_sync(partial(function, *args, **kwargs))

    monkeypatch.setattr(videos.storage_service, "upload_file", racing_upload)
    monkeypatch.setattr(videos, "run_in_threadpool", run_in_worker)

    def complete_winning_job(video_id: str) -> None:
        with storage_lock:
            parse_calls.append(video_id)
        complete_job(session_factory, video_id)

    monkeypatch.setattr(videos, "parse_full", complete_winning_job)

    app = FastAPI()
    app.include_router(videos.router)
    keep_ticking = True

    async def short_ticker():
        # Keep the AnyIO/asyncio loop awake while both worker threads cross the
        # storage barrier.  This avoids a lost worker wake-up in the test
        # runner; production uses Starlette's own thread-pool adapter.
        while keep_ticking:
            await asyncio.sleep(0.01)

    ticker_task = asyncio.create_task(short_ticker())
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            first, second = await asyncio.wait_for(
                asyncio.gather(
                    client.post(
                        "/api/videos/upload-and-parse",
                        files={"file": ("first.mp4", b"first-video", "video/mp4")},
                        data={"task_id": CLIENT_TASK_ID},
                    ),
                    client.post(
                        "/api/videos/upload-and-parse",
                        files={"file": ("second.mp4", b"second-video", "video/mp4")},
                        data={"task_id": CLIENT_TASK_ID},
                    ),
                ),
                timeout=5,
            )
        polled = videos.get_video(CLIENT_TASK_ID)

        assert sorted([first.status_code, second.status_code]) == [200, 409]
        assert len(stored_contents) == 2
        assert len(set(stored_contents)) == 2
        assert all(key.startswith(f"videos/{CLIENT_TASK_ID}/source/") for key in stored_contents)
        assert parse_calls == [CLIENT_TASK_ID]

        job = polled
        assert job["status"] == "completed"
        assert job["source_object_key"] in stored_contents
        expected_content = b"first-video" if job["filename"] == "first.mp4" else b"second-video"
        assert stored_contents[job["source_object_key"]] == expected_content
    finally:
        keep_ticking = False
        await ticker_task
        engine.dispose()


@pytest.mark.anyio
async def test_upload_storage_failure_cleans_temp_file_and_skips_parser(video_api, monkeypatch):
    app, _ = video_api
    upload_paths = []
    parser_called = False

    def failing_upload(*, object_key, path, media_type, bucket=None):
        upload_paths.append(path)
        raise RuntimeError("object storage unavailable")

    def unexpected_parse(video_id: str):
        nonlocal parser_called
        parser_called = True

    monkeypatch.setattr(videos.storage_service, "upload_file", failing_upload)
    monkeypatch.setattr(videos, "parse_full", unexpected_parse)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/videos/upload-and-parse",
            files={"file": ("demo.mp4", b"video-content", "video/mp4")},
        )

    assert response.status_code == 500
    assert response.json() == {
        "video_id": None,
        "mode": "full",
        "success": False,
        "status": "failed",
        "job": None,
        "error": {
            "code": "VIDEO_UPLOAD_FAILED",
            "message": "视频上传或任务创建失败",
        },
    }
    assert parser_called is False
    assert len(upload_paths) == 1
    assert upload_paths[0].exists() is False


def test_upload_and_parse_does_not_accept_background_tasks_dependency():
    parameters = signature(videos.upload_and_parse_video).parameters
    assert "background_tasks" not in parameters


@pytest.mark.anyio
async def test_existing_upload_endpoint_still_returns_uploaded_job(video_api, monkeypatch):
    app, _ = video_api

    def unexpected_parse(video_id: str):
        pytest.fail(f"plain upload must not start parsing: {video_id}")

    monkeypatch.setattr(videos, "parse_full", unexpected_parse)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/videos",
            files={"file": ("demo.mp4", b"video-content", "video/mp4")},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "uploaded"
