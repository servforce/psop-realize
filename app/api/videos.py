from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.core.video_config import video_settings as settings
from app.db.video import VideoSessionLocal
from app.models.video import VideoFrame, VideoJob
from app.services.audit import finish_call, logged_call
from app.services.storage import storage_service
from app.services.transcript_tree import render_transcript_tree_text
from app.services.video_repository import VideoJobRepository
from app.services.video_deletion import VideoDeletionConflict, VideoNotFoundError, delete_video_data
from app.services.video_parsing import (
    parse_full,
    parse_keyframes,
    parse_markdown,
    parse_transcript,
)
from app.services.video_export import build_export_package
from app.services.video_outputs import (
    markdown_object_key,
    semantic_frame_matches_object_key,
    transcript_rendered_object_key,
    transcript_tree_object_key,
)
from app.services.videos import media_type_for_suffix
router = APIRouter(prefix="/api/videos", tags=["videos"])
logger = logging.getLogger(__name__)
SEMANTIC_SECTION_FRAME_DISPLAY_LIMIT = 5

SUPPORTED_SUFFIXES = {".mp4", ".mov", ".webm", ".m4v", ".mkv"}
CLIENT_TASK_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


@router.post("")
async def upload_video(file: UploadFile = File(...), title: str = Form("")):
    return await _store_uploaded_video(
        file=file,
        title=title,
        endpoint="POST /api/videos",
    )


@router.post("/upload-and-parse")
async def upload_and_parse_video(
    file: UploadFile = File(...),
    title: str = Form(""),
    task_id: str | None = Form(None),
):
    """Upload a video, run the full parser, and respond only after parsing ends.

    The HTTP connection intentionally remains open for the whole upload and parse
    workflow.  The synchronous parser runs in Starlette's thread pool so this
    long-lived request does not block the FastAPI event loop.
    """
    requested_video_id = validate_client_task_id(task_id) if task_id is not None else None
    try:
        uploaded = await _store_uploaded_video(
            file=file,
            title=title,
            endpoint="POST /api/videos/upload-and-parse",
            requested_video_id=requested_video_id,
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("video upload or job creation failed")
        return JSONResponse(
            status_code=500,
            content={
                "video_id": None,
                "mode": "full",
                "success": False,
                "status": "failed",
                "job": None,
                "error": {
                    "code": "VIDEO_UPLOAD_FAILED",
                    "message": "视频上传或任务创建失败",
                },
            },
        )

    video_id = str(uploaded["id"])
    # Persist the ownership of this combined request before queueing blocking
    # parser work.  Startup recovery can then turn an interrupted request into
    # `failed`; plain `/api/videos` uploads intentionally remain `uploaded`.
    with VideoSessionLocal() as session:
        VideoJobRepository(session).update_job(
            video_id,
            status="processing",
            stage="transcribing_asr",
            progress=5,
            error_message="",
        )

    parse_error: Exception | None = None
    try:
        await run_in_threadpool(run_video_parse_job, video_id, "full")
    except Exception as exc:
        parse_error = exc
        logger.exception("full video parse failed: video_id=%s", video_id)

    job_payload = _get_video_job_payload(video_id)
    if job_payload.get("status") not in {
        "completed",
        "completed_with_warnings",
        "failed",
    }:
        # The normal parsers persist a terminal status themselves.  Keep the
        # API contract terminal even if a wrapper returns/raises before that
        # update, otherwise GET polling would never finish.
        with VideoSessionLocal() as session:
            VideoJobRepository(session).update_job(
                video_id,
                status="failed",
                stage="failed",
                progress=100,
                error_message="视频解析失败",
                completed=True,
            )
        job_payload = _get_video_job_payload(video_id)
    success = job_payload.get("status") in {"completed", "completed_with_warnings"}
    public_error = "" if success else _public_parse_error(job_payload.get("error_message"), parse_error)
    job_payload["error_message"] = public_error
    result = {
        "video_id": video_id,
        "mode": "full",
        "success": success,
        "status": job_payload.get("status"),
        "job": job_payload,
        "error": None
        if success
        else {
            "code": "VIDEO_PARSE_FAILED",
            "message": public_error or "视频解析未正常完成",
        },
    }
    if success:
        return result
    return JSONResponse(status_code=500, content=result)


async def _store_uploaded_video(
    *,
    file: UploadFile,
    title: str,
    endpoint: str,
    requested_video_id: str | None = None,
) -> dict:
    filename = safe_filename(file.filename or "video.mp4")
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"不支持的视频格式: {suffix or 'unknown'}")

    video_id = requested_video_id or uuid.uuid4().hex
    if requested_video_id is not None:
        with VideoSessionLocal() as session:
            if session.get(VideoJob, video_id) is not None:
                raise HTTPException(status_code=409, detail="task_id 已存在，请为本次任务生成新的 UUID")
    media_type = file.content_type or media_type_for_suffix(suffix)
    # Client-provided IDs can race before the database insert.  A per-attempt
    # object name prevents the losing request from overwriting the winner's
    # source object even though the primary-key insert will later return 409.
    source_filename = f"{uuid.uuid4().hex}_{filename}" if requested_video_id is not None else filename
    object_key = f"videos/{video_id}/source/{source_filename}"
    workdir = Path(settings.video_workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f"upload_{video_id}_", suffix=suffix, dir=str(workdir))
    os.close(fd)
    temp_path = Path(temp_name)
    size = 0
    try:
        with temp_path.open("wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.video_max_upload_bytes:
                    raise HTTPException(status_code=413, detail="视频文件超过上传大小限制")
                output.write(chunk)
        if size <= 0:
            raise HTTPException(status_code=400, detail="上传文件为空")

        stored = await run_in_threadpool(
            storage_service.upload_file,
            object_key=object_key,
            path=temp_path,
            media_type=media_type,
        )
        with VideoSessionLocal() as session:
            with logged_call(
                session,
                interface_type="rest",
                tool_or_endpoint=endpoint,
                request={"filename": filename, "size_bytes": size},
            ) as call_id:
                repo = VideoJobRepository(session)
                try:
                    job = repo.create_job(
                        video_id=video_id,
                        title=title.strip() or Path(filename).stem,
                        filename=filename,
                        content_type=media_type,
                        size_bytes=size,
                        source_bucket=stored.bucket,
                        source_object_key=stored.object_key,
                        # Combined uploads must not expose a deletable `uploaded` gap.
                        status="processing" if endpoint == "POST /api/videos/upload-and-parse" else "uploaded",
                    )
                except IntegrityError as exc:
                    session.rollback()
                    raise HTTPException(status_code=409, detail="task_id 已存在，请为本次任务生成新的 UUID") from exc
                payload = job_to_dict(job)
                payload["call_id"] = call_id
                return payload
    finally:
        temp_path.unlink(missing_ok=True)
        await file.close()


@router.get("")
def list_videos(
    page: int | None = Query(None, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    query: str = Query("", max_length=200),
    status: Literal["", "uploaded", "queued", "processing", "completed", "completed_with_warnings", "failed", "deleting"] = Query(""),
    sort: Literal["asc", "desc"] = Query("desc"),
):
    with VideoSessionLocal() as session:
        repo = VideoJobRepository(session)
        # Keep the original array response for existing API/MCP consumers.
        if page is None:
            return [job_to_dict(item) for item in repo.list_jobs()]
        jobs, total, page, total_pages = repo.paginate_jobs(
            page=page, page_size=page_size, query=query, status=status, sort=sort
        )
        return {"items": [job_to_dict(item) for item in jobs], "total": total,
                "page": page, "page_size": page_size, "total_pages": total_pages}


@router.get("/{video_id}")
def get_video(video_id: str):
    return _get_video_job_payload(video_id)


@router.delete("/{video_id}")
def delete_video(video_id: str):
    try:
        delete_video_data(
            video_id, session_factory=VideoSessionLocal,
            storage=storage_service, default_bucket=settings.object_store_bucket,
        )
    except VideoNotFoundError as exc:
        raise HTTPException(status_code=404, detail="视频任务不存在或已删除") from exc
    except VideoDeletionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="视频标识或存储路径不正确，未完成删除") from exc
    except Exception as exc:
        logger.exception("video deletion incomplete: video_id=%s", video_id)
        raise HTTPException(status_code=503, detail="删除未完成，请重试删除；该任务已暂停解析。") from exc
    return {"id": video_id, "deleted": True}


def _get_video_job_payload(video_id: str) -> dict:
    with VideoSessionLocal() as session:
        repo = VideoJobRepository(session)
        job = repo.get_job(video_id)
        if job is None:
            raise HTTPException(status_code=404, detail="视频任务不存在")
        payload = job_to_dict(job)
        return payload


def _public_parse_error(error_message: object, parse_error: Exception | None) -> str:
    """Return a stable public message without exposing internal exception text."""
    if str(error_message or "").strip() or parse_error is not None:
        return "视频解析失败"
    return ""


def validate_client_task_id(value: str) -> str:
    """Require the exact UUID v4 hex value the client will later use for GET."""
    if not CLIENT_TASK_ID_PATTERN.fullmatch(value):
        raise HTTPException(status_code=400, detail="task_id 必须是 32 位小写 UUID v4 hex 字符串")
    parsed = uuid.UUID(hex=value)
    if parsed.version != 4 or parsed.hex != value:
        raise HTTPException(status_code=400, detail="task_id 必须是 32 位小写 UUID v4 hex 字符串")
    return value


@router.post("/{video_id}/parse")
def parse_video(video_id: str, background_tasks: BackgroundTasks, mode: str = Query("full")):
    if mode not in {"full", "keyframes", "transcript", "markdown"}:
        raise HTTPException(status_code=400, detail="mode must be full, keyframes, transcript, or markdown")
    with VideoSessionLocal() as session:
        with logged_call(
                session,
                interface_type="rest",
                tool_or_endpoint="POST /api/videos/{video_id}/parse",
                request={"video_id": video_id, "mode": mode},
                video_id=video_id,
        ) as call_id:
            job = session.scalar(select(VideoJob).where(VideoJob.id == video_id).with_for_update())
            if job is None:
                raise HTTPException(status_code=404, detail="视频任务不存在")
            if job.status == "deleting":
                raise HTTPException(status_code=409, detail="该视频正在删除或等待重试删除，不能启动解析。")
            if job.status in {"queued", "processing"}:
                raise HTTPException(status_code=409, detail="视频任务正在处理中，请等待任务结束。")
            if mode == "full":
                job.status = "processing"
                job.current_stage = "transcribing_asr"
                job.progress_percent = 5
                job.error_message = ""
                job.completed_at = None
                job.updated_at = datetime.now(timezone.utc)
                session.add(job)
                session.commit()
                session.refresh(job)
                background_tasks.add_task(run_video_parse_job, video_id, mode)
                result = {"video_id": video_id, "mode": mode, "job": job_to_dict(job)}
                finish_call(session, call_id, result)
                return result
            if mode == "keyframes":
                job.status = "processing"
                job.current_stage = "extracting_keyframes"
                job.progress_percent = 20
                job.error_message = ""
                job.completed_at = None
                job.updated_at = datetime.now(timezone.utc)
                session.add(job)
                session.commit()
                session.refresh(job)
                background_tasks.add_task(run_video_parse_job, video_id, mode)
                result = {"video_id": video_id, "mode": mode, "job": job_to_dict(job)}
                finish_call(session, call_id, result)
                return result
            job.status = "processing"
            job.current_stage = "generating_markdown" if mode == "markdown" else "transcribing_asr"
            job.progress_percent = 88 if mode == "markdown" else 62
            job.error_message = ""
            job.completed_at = None
            job.updated_at = datetime.now(timezone.utc)
            session.add(job)
            session.commit()
            session.refresh(job)
            background_tasks.add_task(run_video_parse_job, video_id, mode)
            result = {"video_id": video_id, "mode": mode, "job": job_to_dict(job)}
            finish_call(session, call_id, result)
            return result


def run_video_parse_job(video_id: str, mode: str) -> None:
    with VideoSessionLocal() as session:
        with logged_call(
            session,
            interface_type="background",
            tool_or_endpoint="video_parse_job",
            request={"video_id": video_id, "mode": mode},
            video_id=video_id,
        ) as call_id:
            if mode == "full":
                parse_full(video_id)
            elif mode == "keyframes":
                parse_keyframes(video_id)
            elif mode == "transcript":
                parse_transcript(video_id)
            elif mode == "markdown":
                parse_markdown(video_id)
            else:
                raise ValueError(f"unsupported parse mode: {mode}")

            session.expire_all()
            repo = VideoJobRepository(session)
            job = repo.get_job(video_id)
            if job is None:
                result = {"video_id": video_id, "mode": mode, "job": None}
            else:
                result = {"video_id": video_id, "mode": mode, "job": job_to_dict(job)}
            finish_call(session, call_id, result)


@router.get("/{video_id}/frames")
def list_frames(video_id: str):
    with VideoSessionLocal() as session:
        repo = VideoJobRepository(session)
        job = repo.get_job(video_id)
        if job is None:
            raise HTTPException(status_code=404, detail="视频任务不存在")
        return [frame_to_dict(video_id, item) for item in repo.frames_for_video(video_id)]


@router.get("/{video_id}/semantic-frames")
def get_semantic_frames(video_id: str):
    with VideoSessionLocal() as session:
        repo = VideoJobRepository(session)
        job = repo.get_job(video_id)
        if job is None:
            raise HTTPException(status_code=404, detail="视频任务不存在")
        try:
            report = json.loads(
                storage_service.get_bytes(
                    bucket=job.source_bucket,
                    object_key=semantic_frame_matches_object_key(video_id),
                ).decode("utf-8", errors="replace")
            )
        except Exception:
            frames = repo.frames_for_video(video_id)
            tree = read_latest_transcript_tree(session, video_id)
            sections = ((tree.get("tree") or {}).get("sections") or []) if isinstance(tree, dict) else []
            return {
                "version": "fallback",
                "video_id": video_id,
                "summary": {
                    "raw_frame_count": len(frames),
                    "candidate_frame_count": 0,
                    "section_count": len(sections),
                },
                "sections": [
                    {
                        "index": section.get("index"),
                        "title": section.get("title") or "",
                        "text": section.get("text") or "",
                        "polished_text": section.get("polished_text") or "",
                        "business_frame_text": section.get("business_frame_text") or "",
                        "query_graph": section.get("query_graph") if isinstance(section.get("query_graph"), dict) else {},
                        "start_seconds": section.get("start_seconds"),
                        "end_seconds": section.get("end_seconds"),
                        "start_time": section.get("start_time"),
                        "end_time": section.get("end_time"),
                        "query_graph_matches": [],
                        "raw_frames_total": len(frames_in_section(video_id, frames, section)),
                        "raw_frames": frames_in_section(video_id, frames, section)[:SEMANTIC_SECTION_FRAME_DISPLAY_LIMIT],
                        "semantic_frames": [],
                    }
                    for section in sections
                    if isinstance(section, dict)
                ],
                "candidate_frames": [],
            }
        return limit_semantic_frame_report(report, limit=SEMANTIC_SECTION_FRAME_DISPLAY_LIMIT)


def limit_semantic_frame_report(report: dict, *, limit: int) -> dict:
    if not isinstance(report, dict):
        return report
    sections = report.get("sections")
    if not isinstance(sections, list):
        return report
    for section in sections:
        if not isinstance(section, dict):
            continue
        for key in ("raw_frames", "candidate_business_frames", "semantic_frames"):
            items = section.get(key)
            if isinstance(items, list):
                section[f"{key}_total"] = len(items)
                section[key] = items[:limit]
        matches = section.get("query_graph_matches")
        if not isinstance(matches, list):
            continue
        for match in matches:
            if not isinstance(match, dict):
                continue
            frames = match.get("frames")
            if isinstance(frames, list):
                match["frames_total"] = len(frames)
                match["frames"] = frames[:limit]
    summary = report.setdefault("summary", {})
    if isinstance(summary, dict):
        summary["section_frame_display_limit"] = limit
    return report


@router.get("/{video_id}/frames/{filename}")
def get_frame(video_id: str, filename: str):
    safe_name = Path(filename).name
    if safe_name != filename or Path(safe_name).suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        raise HTTPException(status_code=400, detail="invalid frame filename")
    object_key = f"videos/{video_id}/frames/{safe_name}"
    try:
        content = storage_service.get_bytes(bucket=settings.object_store_bucket, object_key=object_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="关键帧不存在") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"关键帧存储读取失败: {exc}") from exc
    media_type = "image/png" if safe_name.lower().endswith(".png") else "image/jpeg"
    return Response(content=content, media_type=media_type)


@router.get("/{video_id}/transcript")
def get_transcript(video_id: str):
    with VideoSessionLocal() as session:
        repo = VideoJobRepository(session)
        job = repo.get_job(video_id)
        if job is None:
            raise HTTPException(status_code=404, detail="视频任务不存在")
        if not job.transcript_object_key:
            return {"text": "", "rendered_text": ""}
        try:
            tree = json.loads(read_object_text(job.source_bucket, transcript_tree_object_key(video_id)))
        except Exception:
            tree = {}
        if isinstance(tree, dict) and tree:
            rendered_text = render_transcript_tree_text(tree)
            return {"text": rendered_text, "rendered_text": rendered_text}
        rendered_key = job.transcript_object_key or transcript_rendered_object_key(video_id)
        rendered_text = read_object_text(job.source_bucket, rendered_key)
        return {"text": rendered_text, "rendered_text": rendered_text}

@router.get("/{video_id}/export")
def export_video_package(video_id: str):
    """导出自包含压缩包:result.md + frames/ 业务帧图片 + manifest.json。

    每个段落一张分数最高的业务帧,markdown 用包内相对路径引用,
    解压即可离线阅读,调用方无需了解底层存储实现。
    """
    with VideoSessionLocal() as session:
        job = session.get(VideoJob, video_id)
        if job is None:
            raise HTTPException(status_code=404, detail="视频任务不存在")
        try:
            content = build_export_package(job)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"导出打包失败: {exc}") from exc
        return Response(
            content=content,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="video_{video_id}.zip"'},
        )


@router.get("/{video_id}/markdown", response_class=PlainTextResponse)
def get_markdown(video_id: str):
    with VideoSessionLocal() as session:
        job = session.get(VideoJob, video_id)
        if job is None:
            raise HTTPException(status_code=404, detail="视频任务不存在")
        target_key = job.markdown_object_key or markdown_object_key(video_id)
        if not target_key:
            return ""
        return read_object_text(job.source_bucket, target_key)


def safe_filename(value: str) -> str:
    name = value.replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", name)
    return name or "video.mp4"


def job_to_dict(job: VideoJob) -> dict:
    public_error_message = job.error_message or ""
    if job.status == "failed":
        public_error_message = "视频解析失败"
    return {
        "id": job.id,
        "title": job.title,
        "filename": job.filename,
        "content_type": job.content_type,
        "size_bytes": job.size_bytes,
        "status": job.status,
        "progress_percent": job.progress_percent,
        "current_stage": job.current_stage,
        "stage_processed": job.stage_processed,
        "stage_total": job.stage_total,
        "stage_message": job.stage_message or "",
        "error_message": public_error_message,
        "duration_ms": job.duration_ms,
        "frame_count": job.frame_count,
        "source_bucket": job.source_bucket,
        "source_object_key": job.source_object_key,
        "analysis_video_bucket": job.analysis_video_bucket,
        "analysis_video_object_key": job.analysis_video_object_key,
        "analysis_video_codec": job.analysis_video_codec,
        "analysis_video_height": job.analysis_video_height,
        "analysis_video_size_bytes": job.analysis_video_size_bytes,
        "transcript_object_key": job.transcript_object_key,
        "markdown_object_key": job.markdown_object_key,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def frame_to_dict(video_id: str, frame: VideoFrame) -> dict:
    filename = frame.object_key.rsplit("/", 1)[-1]
    return {
        "id": frame.id,
        "video_id": frame.video_id,
        "timestamp_ms": frame.timestamp_ms,
        "timestamp_seconds": frame.timestamp_seconds,
        "frame_source": frame.frame_source,
        "object_key": frame.object_key,
        "url": f"/api/videos/{video_id}/frames/{filename}",
        "caption": frame.caption or "",
        "selection_status": frame.selection_status or "pending",
        "selection_score": frame.selection_score,
        "selection_reason": frame.selection_reason or "",
        "selection_details_json": frame.selection_details_json or "{}",
        "matched_section_index": frame.matched_section_index,
    }


def frames_in_section(video_id: str, frames: list[VideoFrame], section: dict) -> list[dict]:
    start = parse_optional_float(section.get("start_seconds"))
    end = parse_optional_float(section.get("end_seconds"))
    if start is None or end is None or end <= start:
        return [frame_to_dict(video_id, frame) for frame in frames]
    return [
        frame_to_dict(video_id, frame)
        for frame in frames
        if start <= float(frame.timestamp_seconds or frame.timestamp_ms / 1000 or 0) <= end
    ]


def parse_optional_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_latest_transcript_tree(session, video_id: str) -> dict:
    job = session.get(VideoJob, video_id)
    if job is None:
        return {}
    try:
        return json.loads(read_object_text(job.source_bucket, transcript_tree_object_key(video_id)))
    except Exception:
        return {}


def read_object_text(bucket: str, object_key: str | None) -> str:
    if not object_key:
        return ""
    try:
        return storage_service.get_bytes(bucket=bucket, object_key=object_key).decode("utf-8", errors="replace")
    except Exception:
        return ""
