from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import func, select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.entities import VideoFrame, VideoJob
from app.services.audit import finish_call, logged_call
from app.services.storage import storage_service
from app.services.video_repository import VideoJobRepository
from app.services.video_parsing import (
    latest_wireframes_for_video,
    parse_full,
    parse_keyframes,
    parse_markdown,
    parse_transcript,
    parse_wireframes,
)
from app.services.video_outputs import (
    markdown_object_key,
    semantic_frame_matches_object_key,
    transcript_rendered_object_key,
    transcript_tree_object_key,
)
from app.services.videos import media_type_for_suffix
from app.services.wireframe_jobs import (
    create_wireframe_job,
    get_wireframe_job,
    latest_wireframe_job,
    wireframe_job_to_dict,
)
router = APIRouter(prefix="/api/videos", tags=["videos"])

SUPPORTED_SUFFIXES = {".mp4", ".mov", ".webm", ".m4v", ".mkv"}


@router.post("")
async def upload_video(file: UploadFile = File(...), title: str = Form("")):
    filename = safe_filename(file.filename or "video.mp4")
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"涓嶆敮鎸佺殑瑙嗛鏍煎紡: {suffix or 'unknown'}")

    video_id = uuid.uuid4().hex
    media_type = file.content_type or media_type_for_suffix(suffix)
    object_key = f"videos/{video_id}/source/{filename}"
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
                    raise HTTPException(status_code=413, detail="瑙嗛鏂囦欢瓒呰繃涓婁紶澶у皬闄愬埗")
                output.write(chunk)
        if size <= 0:
            raise HTTPException(status_code=400, detail="涓婁紶鏂囦欢涓虹┖")

        stored = storage_service.upload_file(object_key=object_key, path=temp_path, media_type=media_type)
        with SessionLocal() as session:
            with logged_call(
                session,
                interface_type="rest",
                tool_or_endpoint="POST /api/videos",
                request={"filename": filename, "size_bytes": size},
            ) as call_id:
                repo = VideoJobRepository(session)
                job = repo.create_job(
                    video_id=video_id,
                    title=title.strip() or Path(filename).stem,
                    filename=filename,
                    content_type=media_type,
                    size_bytes=size,
                    source_bucket=stored.bucket,
                    source_object_key=stored.object_key,
                )
                payload = job_to_dict(job)
                payload["call_id"] = call_id
                return payload
    finally:
        temp_path.unlink(missing_ok=True)


@router.get("")
def list_videos():
    with SessionLocal() as session:
        repo = VideoJobRepository(session)
        return [job_to_dict(item, wireframe_count=count_wireframes(session, item.id)) for item in repo.list_jobs()]


@router.get("/{video_id}")
def get_video(video_id: str):
    with SessionLocal() as session:
        repo = VideoJobRepository(session)
        job = repo.get_job(video_id)
        if job is None:
            raise HTTPException(status_code=404, detail="视频任务不存在")
        payload = job_to_dict(job, wireframe_count=count_wireframes(session, video_id))
        return payload


@router.post("/{video_id}/parse")
def parse_video(video_id: str, background_tasks: BackgroundTasks, mode: str = Query("full")):
    if mode not in {"full", "keyframes", "wireframes", "transcript", "markdown"}:
        raise HTTPException(status_code=400, detail="mode must be full, keyframes, wireframes, transcript, or markdown")
    with SessionLocal() as session:
        with logged_call(
                session,
                interface_type="rest",
                tool_or_endpoint="POST /api/videos/{video_id}/parse",
                request={"video_id": video_id, "mode": mode},
                video_id=video_id,
        ) as call_id:
            job = session.get(VideoJob, video_id)
            if job is None:
                raise HTTPException(status_code=404, detail="视频任务不存在")
            if mode == "wireframes":
                if job.frame_count <= 0:
                    raise HTTPException(status_code=400, detail="瑙嗛杩樻病鏈夊叧閿抚锛岃鍏堣В鏋愬叧閿抚")
                wireframe_job = create_wireframe_job(video_id)
                refreshed_wireframe_job = get_wireframe_job(wireframe_job.id) or wireframe_job
                background_tasks.add_task(run_video_parse_job, video_id, mode, wireframe_job.id)
                result = {
                    "video_id": video_id,
                    "mode": mode,
                    "job": job_to_dict(job, wireframe_count=count_wireframes(session, video_id)),
                    "wireframe_job": wireframe_job_to_dict(refreshed_wireframe_job),
                }
                finish_call(session, call_id, result)
                return result
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
                result = {"video_id": video_id, "mode": mode, "job": job_to_dict(job, wireframe_count=count_wireframes(session, video_id))}
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
                result = {"video_id": video_id, "mode": mode, "job": job_to_dict(job, wireframe_count=count_wireframes(session, video_id))}
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
            result = {"video_id": video_id, "mode": mode, "job": job_to_dict(job, wireframe_count=count_wireframes(session, video_id))}
            finish_call(session, call_id, result)
            return result


def run_video_parse_job(video_id: str, mode: str, wireframe_job_id: str | None = None) -> None:
    with SessionLocal() as session:
        with logged_call(
            session,
            interface_type="background",
            tool_or_endpoint="video_parse_job",
            request={"video_id": video_id, "mode": mode, "wireframe_job_id": wireframe_job_id},
            video_id=video_id,
        ) as call_id:
            if mode == "full":
                parse_full(video_id)
            elif mode == "keyframes":
                parse_keyframes(video_id)
            elif mode == "wireframes":
                parse_wireframes(video_id, wireframe_job_id=wireframe_job_id)
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
                result = {"video_id": video_id, "mode": mode, "job": job_to_dict(job, wireframe_count=count_wireframes(session, video_id))}
            if wireframe_job_id:
                wireframe_job = get_wireframe_job(wireframe_job_id)
                result["wireframe_job"] = wireframe_job_to_dict(wireframe_job) if wireframe_job is not None else None
            finish_call(session, call_id, result)


@router.get("/{video_id}/frames")
def list_frames(video_id: str):
    with SessionLocal() as session:
        repo = VideoJobRepository(session)
        job = repo.get_job(video_id)
        if job is None:
            raise HTTPException(status_code=404, detail="视频任务不存在")
        return [frame_to_dict(video_id, item) for item in repo.frames_for_video(video_id)]


@router.get("/{video_id}/semantic-frames")
def get_semantic_frames(video_id: str):
    with SessionLocal() as session:
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
                        "start_seconds": section.get("start_seconds"),
                        "end_seconds": section.get("end_seconds"),
                        "start_time": section.get("start_time"),
                        "end_time": section.get("end_time"),
                        "visual_operations": section.get("visual_operations") if isinstance(section.get("visual_operations"), list) else [],
                        "frame_query_matches": [],
                        "raw_frames": frames_in_section(video_id, frames, section),
                        "semantic_frames": [],
                    }
                    for section in sections
                    if isinstance(section, dict)
                ],
                "candidate_frames": [],
            }
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
        raise HTTPException(status_code=404, detail="鍏抽敭甯т笉瀛樺湪") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"鍏抽敭甯у瓨鍌ㄨ鍙栧け璐? {exc}") from exc
    media_type = "image/png" if safe_name.lower().endswith(".png") else "image/jpeg"
    return Response(content=content, media_type=media_type)


@router.get("/{video_id}/wireframes/jobs/latest")
def get_latest_wireframe_job(video_id: str):
    job = latest_wireframe_job(video_id)
    if job is None:
        return {"video_id": video_id, "job": None}
    return {"video_id": video_id, "job": wireframe_job_to_dict(job)}


@router.get("/{video_id}/wireframes/jobs/{job_id}")
def get_video_wireframe_job(video_id: str, job_id: str):
    job = get_wireframe_job(job_id)
    if job is None or job.video_id != video_id:
        raise HTTPException(status_code=404, detail="绾挎鍥句换鍔′笉瀛樺湪")
    return {"video_id": video_id, "job": wireframe_job_to_dict(job)}


@router.get("/{video_id}/wireframes")
def list_wireframes(video_id: str):
    with SessionLocal() as session:
        repo = VideoJobRepository(session)
        job = repo.get_job(video_id)
        if job is None:
            raise HTTPException(status_code=404, detail="视频任务不存在")
        if not settings.video_wireframe_generation_enabled:
            tree = read_latest_transcript_tree(session, video_id)
            section_lookup = transcript_section_lookup(tree)
            frames = [
                frame
                for frame in repo.frames_for_video(video_id)
                if bool(frame.selected_for_wireframe) and (frame.selection_status or "") == "selected"
            ]
            return [selection_preview_to_dict(video_id, item, section_lookup) for item in frames]
        artifacts = latest_wireframes_for_video(
            session,
            video_id,
            frames=repo.frames_for_video(video_id),
            bucket=job.source_bucket,
        )
        return [wireframe_to_dict(item) for item in artifacts]


@router.get("/{video_id}/transcript")
def get_transcript(video_id: str):
    with SessionLocal() as session:
        repo = VideoJobRepository(session)
        job = repo.get_job(video_id)
        if job is None:
            raise HTTPException(status_code=404, detail="视频任务不存在")
        if not job.transcript_object_key:
            return {"text": "", "rendered_text": ""}
        rendered_key = job.transcript_object_key or transcript_rendered_object_key(video_id)
        rendered_text = read_object_text(job.source_bucket, rendered_key)
        return {"text": rendered_text, "rendered_text": rendered_text}

@router.get("/{video_id}/markdown", response_class=PlainTextResponse)
def get_markdown(video_id: str):
    with SessionLocal() as session:
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


def count_wireframes(session, video_id: str) -> int:
    selected_count = int(
        session.scalar(
            select(func.count()).select_from(VideoFrame).where(
                VideoFrame.video_id == video_id,
                VideoFrame.selected_for_wireframe == True,
                VideoFrame.selection_status == "selected",
            )
        )
        or 0
    )
    if not settings.video_wireframe_generation_enabled:
        return selected_count
    job = latest_wireframe_job(video_id)
    if job is None:
        return 0
    completed = int(job.completed_frames or 0)
    if job.status == "completed" and completed <= 0:
        return selected_count
    return max(0, min(selected_count, completed))


def job_to_dict(job: VideoJob, *, wireframe_count: int = 0) -> dict:
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
        "error_message": job.error_message or "",
        "duration_ms": job.duration_ms,
        "frame_count": job.frame_count,
        "wireframe_count": wireframe_count,
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
        "selected_for_wireframe": bool(frame.selected_for_wireframe),
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


def wireframe_to_dict(artifact: dict) -> dict:
    return {
        "id": artifact.get("id"),
        "video_id": artifact.get("video_id"),
        "bucket": artifact.get("bucket"),
        "object_key": artifact.get("object_key"),
        "url": artifact.get("url") or f"/api/objects/{artifact.get('object_key')}",
        "media_type": artifact.get("media_type") or "image/png",
        "size_bytes": artifact.get("size_bytes") or 0,
        "created_at": artifact.get("created_at"),
    }


def selection_preview_to_dict(video_id: str, frame: VideoFrame, section_lookup: dict[int, dict]) -> dict:
    filename = frame.object_key.rsplit("/", 1)[-1]
    details = parse_json_object(frame.selection_details_json)
    matched_section_index = frame.matched_section_index
    section = section_lookup.get(matched_section_index or -1, {})
    visual_evidence = details.get("visual_evidence")
    if not isinstance(visual_evidence, list):
        visual_evidence = []
    return {
        "id": frame.id,
        "kind": "selection_preview",
        "video_id": frame.video_id,
        "frame_id": frame.id,
        "timestamp_ms": frame.timestamp_ms,
        "timestamp_seconds": frame.timestamp_seconds,
        "object_key": frame.object_key,
        "url": f"/api/videos/{video_id}/frames/{filename}",
        "source_frame_url": f"/api/videos/{video_id}/frames/{filename}",
        "selected_for_wireframe": bool(frame.selected_for_wireframe),
        "selection_status": frame.selection_status or "pending",
        "selection_score": frame.selection_score,
        "selection_reason": frame.selection_reason or "",
        "matched_section_index": matched_section_index,
        "matched_section_title": section.get("title") or details.get("matched_section_title") or "",
        "matched_section_text": section.get("text") or "",
        "visual_evidence": visual_evidence,
        "wireframe_generation_enabled": False,
    }


def read_latest_transcript_tree(session, video_id: str) -> dict:
    job = session.get(VideoJob, video_id)
    if job is None:
        return {}
    try:
        return json.loads(read_object_text(job.source_bucket, transcript_tree_object_key(video_id)))
    except Exception:
        return {}


def transcript_section_lookup(tree: dict) -> dict[int, dict]:
    sections = ((tree.get("tree") or {}).get("sections") or []) if isinstance(tree, dict) else []
    lookup: dict[int, dict] = {}
    for section in sections:
        if not isinstance(section, dict):
            continue
        try:
            index = int(section.get("index"))
        except (TypeError, ValueError):
            continue
        lookup[index] = section
    return lookup


def parse_json_object(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def read_object_text(bucket: str, object_key: str | None) -> str:
    if not object_key:
        return ""
    try:
        return storage_service.get_bytes(bucket=bucket, object_key=object_key).decode("utf-8", errors="replace")
    except Exception:
        return ""


