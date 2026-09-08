from __future__ import annotations

import base64
import contextlib
import mimetypes
import os
import time
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route


DEFAULT_API_BASE_URL = "http://127.0.0.1:8090"
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_TIMEOUT_SECONDS = 3600.0
DEFAULT_MCP_HOST = "0.0.0.0"
DEFAULT_MCP_PORT = 8100
DEFAULT_MCP_PATH = "/mcp"

mcp = FastMCP("psop-realize", stateless_http=True, json_response=True)
mcp.settings.streamable_http_path = os.getenv("PSOP_REALIZE_MCP_PATH", DEFAULT_MCP_PATH)


class BearerTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        expected = os.getenv("PSOP_REALIZE_MCP_BEARER_TOKEN", "").strip()
        if not expected or request.url.path == "/health":
            return await call_next(request)
        actual = request.headers.get("authorization", "")
        if actual != f"Bearer {expected}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "app": "psop-realize-mcp"})


@contextlib.asynccontextmanager
async def lifespan(_app: Starlette):
    async with mcp.session_manager.run():
        yield


@mcp.tool()
def upload_and_parse_video(
    file_path: str = "",
    file_base64: str = "",
    filename: str = "",
    title: str = "",
    parse_mode: str = "full",
    wait_until_done: bool = True,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    api_base_url: str = "",
) -> dict[str, Any]:
    """Upload a video, start parsing, and optionally wait for completion."""
    if parse_mode not in {"full", "keyframes", "transcript", "markdown"}:
        raise ValueError("parse_mode must be one of: full, keyframes, transcript, markdown")

    video_bytes, resolved_filename, media_type = read_upload_input(
        file_path=file_path,
        file_base64=file_base64,
        filename=filename,
        default_filename="video.mp4",
    )
    if not media_type:
        media_type = guess_media_type(resolved_filename, default="video/mp4")

    base_url = normalized_base_url(api_base_url)
    with httpx.Client(timeout=None, trust_env=False) as client:
        upload_response = client.post(
            f"{base_url}/api/videos",
            data={"title": title or Path(resolved_filename).stem},
            files={"file": (resolved_filename, video_bytes, media_type)},
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        upload_response.raise_for_status()
        response_base_url = public_base_url(base_url)
        upload_payload = absolutize_urls(upload_response.json(), response_base_url)
        video_id = str(upload_payload.get("id") or "")
        if not video_id:
            raise RuntimeError("upload response did not include a video id")

        parse_response = client.post(
            f"{base_url}/api/videos/{video_id}/parse",
            params={"mode": parse_mode},
            timeout=60.0,
        )
        parse_response.raise_for_status()
        parse_payload = absolutize_urls(parse_response.json(), response_base_url)

        final_job = parse_payload.get("job") or upload_payload
        timed_out = False
        if wait_until_done:
            final_job, timed_out = wait_for_video_job(
                client=client,
                request_base_url=base_url,
                response_base_url=response_base_url,
                video_id=video_id,
                timeout_seconds=timeout_seconds,
            )

    return {
        "video_id": video_id,
        "parse_mode": parse_mode,
        "upload": upload_payload,
        "parse": parse_payload,
        "job": final_job,
        "status": final_job.get("status") if isinstance(final_job, dict) else None,
        "progress_percent": final_job.get("progress_percent") if isinstance(final_job, dict) else None,
        "current_stage": final_job.get("current_stage") if isinstance(final_job, dict) else None,
        "timed_out": timed_out,
        "export_tool": "export_video_result",
    }


@mcp.tool()
def export_video_result(
    video_id: str,
    output_path: str = "",
    include_zip_base64: bool = True,
    overwrite: bool = False,
    api_base_url: str = "",
) -> dict[str, Any]:
    """Export parsed Markdown, selected frame images, and manifest as a zip package."""
    if not video_id.strip():
        raise ValueError("video_id is required")

    base_url = normalized_base_url(api_base_url)
    with httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS, trust_env=False) as client:
        response = client.get(f"{base_url}/api/videos/{video_id}/export")
        response.raise_for_status()
        content = response.content

    filename = f"video_{video_id}.zip"
    saved_path = ""
    if output_path.strip():
        saved_path = save_bytes(
            content=content,
            output_path=output_path,
            default_filename=filename,
            overwrite=overwrite,
        )

    payload: dict[str, Any] = {
        "video_id": video_id,
        "filename": filename,
        "media_type": response.headers.get("content-type", "application/zip"),
        "size_bytes": len(content),
        "saved_path": saved_path,
    }
    if include_zip_base64:
        payload["zip_base64"] = base64.b64encode(content).decode("ascii")
    return payload


@mcp.tool()
def detect_image_objects_and_mask(
    file_path: str = "",
    image_base64: str = "",
    filename: str = "",
    api_base_url: str = "",
) -> dict[str, Any]:
    """Upload one image and return detection boxes, mask image, result JSON, objects, and model info."""
    image_bytes, resolved_filename, media_type = read_upload_input(
        file_path=file_path,
        file_base64=image_base64,
        filename=filename,
        default_filename="image.png",
    )
    if not media_type:
        media_type = guess_media_type(resolved_filename, default="image/png")

    base_url = normalized_base_url(api_base_url)
    with httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS, trust_env=False) as client:
        response = client.post(
            f"{base_url}/api/semantic-frames/image",
            files={"file": (resolved_filename, image_bytes, media_type)},
        )
        response.raise_for_status()
        return absolutize_urls(response.json(), public_base_url(base_url))


def wait_for_video_job(
    *,
    client: httpx.Client,
    request_base_url: str,
    response_base_url: str,
    video_id: str,
    timeout_seconds: float,
) -> tuple[dict[str, Any], bool]:
    deadline = time.monotonic() + max(1.0, float(timeout_seconds or DEFAULT_TIMEOUT_SECONDS))
    latest: dict[str, Any] = {}
    while True:
        response = client.get(f"{request_base_url}/api/videos/{video_id}", timeout=60.0)
        response.raise_for_status()
        latest = absolutize_urls(response.json(), response_base_url)
        if latest.get("status") in {"completed", "failed"}:
            return latest, False
        if time.monotonic() >= deadline:
            return latest, True
        time.sleep(DEFAULT_POLL_INTERVAL_SECONDS)


def read_upload_input(
    *,
    file_path: str,
    file_base64: str,
    filename: str,
    default_filename: str,
) -> tuple[bytes, str, str]:
    if file_path.strip():
        path = Path(file_path).expanduser()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"file_path does not exist or is not a file: {file_path}")
        return path.read_bytes(), filename.strip() or path.name, guess_media_type(path.name, default="")

    if file_base64.strip():
        try:
            content = base64.b64decode(file_base64, validate=True)
        except Exception as exc:
            raise ValueError("file_base64/image_base64 is not valid base64") from exc
        if not content:
            raise ValueError("base64 input decoded to an empty file")
        return content, filename.strip() or default_filename, guess_media_type(filename or default_filename, default="")

    raise ValueError("provide either file_path or base64 content")


def normalized_base_url(value: str = "") -> str:
    return (value or os.getenv("PSOP_REALIZE_VIDEO_API_BASE_URL") or DEFAULT_API_BASE_URL).rstrip("/")


def public_base_url(fallback: str) -> str:
    return (os.getenv("PSOP_REALIZE_VIDEO_PUBLIC_BASE_URL") or fallback).rstrip("/")


def guess_media_type(filename: str, *, default: str) -> str:
    media_type, _encoding = mimetypes.guess_type(filename)
    return media_type or default


def absolutize_urls(value: Any, base_url: str) -> Any:
    if isinstance(value, dict):
        return {key: absolutize_urls_for_key(key, item, base_url) for key, item in value.items()}
    if isinstance(value, list):
        return [absolutize_urls(item, base_url) for item in value]
    return value


def absolutize_urls_for_key(key: str, value: Any, base_url: str) -> Any:
    if isinstance(value, str) and key.endswith("_url") and value.startswith("/"):
        return f"{base_url}{value}"
    if isinstance(value, str) and key == "url" and value.startswith("/"):
        return f"{base_url}{value}"
    return absolutize_urls(value, base_url)


def save_bytes(*, content: bytes, output_path: str, default_filename: str, overwrite: bool) -> str:
    path = Path(output_path).expanduser()
    if path.exists() and path.is_dir():
        path = path / default_filename
    elif output_path.endswith(("/", "\\")):
        path = path / default_filename
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"output_path already exists: {path}")
    path.write_bytes(content)
    return str(path.resolve())


def create_mcp_app() -> Starlette:
    return Starlette(
        routes=[
            Route("/health", health),
            Mount("/", app=mcp.streamable_http_app()),
        ],
        middleware=[Middleware(BearerTokenMiddleware)],
        lifespan=lifespan,
    )


app = create_mcp_app()


if __name__ == "__main__":
    transport = os.getenv("PSOP_REALIZE_MCP_TRANSPORT", "streamable-http")
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        import uvicorn

        uvicorn.run(
            "app.mcp_server:app",
            host=os.getenv("PSOP_REALIZE_MCP_HOST", DEFAULT_MCP_HOST),
            port=int(os.getenv("PSOP_REALIZE_MCP_PORT", str(DEFAULT_MCP_PORT))),
        )
