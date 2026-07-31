from __future__ import annotations

import json
import tempfile
import traceback
from pathlib import Path

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.entities import VideoFrame, VideoJob
from app.services.storage import storage_service
from app.services.video_repository import VideoJobRepository
from app.services.transcript_tree import (
    attach_media_to_transcript_tree,
    build_markdown_from_transcript_tree,
    build_structured_transcript,
    render_transcript_tree_text,
)
from app.services.video_outputs import (
    analysis_proxy_video_object_key,
    frame_dedup_report_object_key,
    frame_quality_report_object_key,
    generated_wireframe_refs,
    markdown_object_key,
    semantic_frame_matches_object_key,
    transcript_rendered_object_key,
    transcript_tree_object_key,
)
from app.services.wireframe_jobs import create_wireframe_job, get_wireframe_job, latest_wireframe_job, run_wireframe_job
from app.services.semantic_frames import build_semantic_frame_match_report
from app.services.videos import (
    extract_candidate_frames,
    generate_analysis_proxy_video,
    parse_timestamp_from_frame_name,
    probe_video_duration_ms,
    transcribe_or_fallback,
)


def ensure_analysis_video_file(
    *,
    repo: VideoJobRepository,
    job: VideoJob,
    source_path: Path,
    output_path: Path,
) -> tuple[Path, VideoJob]:
    if job.analysis_video_bucket and job.analysis_video_object_key:
        try:
            storage_service.download_file(
                bucket=job.analysis_video_bucket,
                object_key=job.analysis_video_object_key,
                path=output_path,
            )
            if output_path.exists() and output_path.stat().st_size > 0:
                return output_path, job
        except Exception:
            pass

    if not source_path.exists():
        storage_service.download_file(bucket=job.source_bucket, object_key=job.source_object_key, path=source_path)
    repo.update_job(
        job.id,
        status="processing",
        stage="preparing_analysis_proxy",
        stage_message="Generating 720P H.265 analysis proxy video",
    )
    generate_analysis_proxy_video(source_path, output_path)
    stored = storage_service.upload_file(
        object_key=analysis_proxy_video_object_key(job.id),
        path=output_path,
        media_type="video/mp4",
        bucket=job.source_bucket,
    )
    updated = repo.update_job(
        job.id,
        analysis_video_bucket=stored.bucket,
        analysis_video_object_key=stored.object_key,
        analysis_video_codec="h265",
        analysis_video_height=settings.video_analysis_proxy_height,
        analysis_video_size_bytes=stored.size_bytes,
        stage_message="720P H.265 analysis proxy video is ready",
    )
    return output_path, updated


def parse_keyframes(video_id: str, *, finalize: bool = True) -> None:
    with SessionLocal() as session:
        repo = VideoJobRepository(session)
        job = repo.get_job(video_id)
        if job is None:
            return
        try:
            Path(settings.video_workdir).mkdir(parents=True, exist_ok=True)
            repo.update_job(video_id, status="processing", stage="extracting_keyframes", progress=20, error_message="")
            with tempfile.TemporaryDirectory(prefix=f"{video_id}_parse_frames_", dir=settings.video_workdir) as tmp:
                source_path = Path(tmp) / f"source{Path(job.filename).suffix or '.mp4'}"
                frame_dir = Path(tmp) / "frames"
                analysis_path, job = ensure_analysis_video_file(
                    repo=repo,
                    job=job,
                    source_path=source_path,
                    output_path=Path(tmp) / "analysis_720p_h265.mp4",
                )
                duration_ms = job.duration_ms or probe_video_duration_ms(analysis_path)
                if duration_ms and duration_ms != job.duration_ms:
                    job = repo.update_job(video_id, duration_ms=duration_ms)
                tree_key = transcript_tree_object_key(video_id)
                try:
                    transcript_tree = json.loads(
                        storage_service.get_bytes(bucket=job.source_bucket, object_key=tree_key).decode(
                            "utf-8",
                            errors="replace",
                        )
                    )
                except Exception as exc:
                    raise RuntimeError("视频还没有结构化转写结果，请先点击“转写文本（包括结构化文本）”。") from exc
                extracted_frames = extract_candidate_frames(
                    analysis_path,
                    frame_dir,
                    interval_seconds=settings.video_dense_frame_interval_seconds,
                )
                frame_rows: list[VideoFrame] = []
                frame_paths_by_object_key: dict[str, Path] = {}
                for frame_path in extracted_frames:
                    timestamp_ms = parse_timestamp_from_frame_name(frame_path.name)
                    object_key = f"videos/{video_id}/frames/{frame_path.name}"
                    stored = storage_service.upload_file(object_key=object_key, path=frame_path, media_type="image/jpeg")
                    frame_paths_by_object_key[stored.object_key] = frame_path
                    frame_rows.append(
                        VideoFrame(
                            video_id=video_id,
                            timestamp_ms=timestamp_ms,
                            timestamp_seconds=round(timestamp_ms / 1000, 3),
                            frame_source="hybrid_candidate",
                            bucket=stored.bucket,
                            object_key=stored.object_key,
                            size_bytes=stored.size_bytes,
                            caption=f"视频 {format_timestamp(timestamp_ms / 1000)} 关键帧",
                        )
                    )
                repo.replace_frames(video_id, frame_rows)
                frames = repo.frames_for_video(video_id)
                repo.update_job(
                    video_id,
                    status="processing",
                    stage="semantic_matching",
                    progress=58,
                    error_message="正在进行图像质量过滤、HSV+pHash 去重和 qwen3-vl-embedding 语义匹配",
                    frame_count=len(frames),
                    stage_processed=0,
                    stage_total=0,
                    stage_message="",
                )
                last_progress: dict[str, int | str] = {"stage": "", "processed": -1}

                def update_stage_progress(stage: str, processed: int, total: int, message: str) -> None:
                    if last_progress["stage"] == stage and int(last_progress["processed"]) == processed:
                        return
                    last_progress["stage"] = stage
                    last_progress["processed"] = processed
                    progress_by_stage = {
                        "filtering_frames": 48,
                        "deduplicating_frames": 54,
                        "semantic_matching": 62,
                    }
                    repo.update_job(
                        video_id,
                        status="processing",
                        stage=stage,
                        progress=progress_by_stage.get(stage, 58),
                        stage_processed=processed,
                        stage_total=total,
                        stage_message=message,
                    )

                semantic_report, quality_report, dedup_report = build_semantic_frame_match_report(
                    job=job,
                    frames=frames,
                    frame_paths_by_object_key=frame_paths_by_object_key,
                    transcript_tree=transcript_tree,
                    progress_callback=update_stage_progress,
                )
                session.commit()
                storage_service.upload_bytes(
                    object_key=frame_quality_report_object_key(video_id),
                    content=json.dumps(quality_report, ensure_ascii=False, indent=2).encode("utf-8"),
                    media_type="application/json; charset=utf-8",
                    bucket=job.source_bucket,
                )
                storage_service.upload_bytes(
                    object_key=frame_dedup_report_object_key(video_id),
                    content=json.dumps(dedup_report, ensure_ascii=False, indent=2).encode("utf-8"),
                    media_type="application/json; charset=utf-8",
                    bucket=job.source_bucket,
                )
                semantic_report["source"] = {
                    "quality_report_object_key": frame_quality_report_object_key(video_id),
                    "dedup_report_object_key": frame_dedup_report_object_key(video_id),
                    "semantic_frame_matches_object_key": semantic_frame_matches_object_key(video_id),
                }
                storage_service.upload_bytes(
                    object_key=semantic_frame_matches_object_key(video_id),
                    content=json.dumps(semantic_report, ensure_ascii=False, indent=2).encode("utf-8"),
                    media_type="application/json; charset=utf-8",
                    bucket=job.source_bucket,
                )
                if finalize:
                    repo.update_job(
                        video_id,
                        status="completed",
                        stage="completed",
                        progress=100,
                        error_message="",
                        frame_count=len(frame_rows),
                        stage_processed=0,
                        stage_total=0,
                        stage_message="",
                        completed=True,
                    )
                else:
                    repo.update_job(
                        video_id,
                        status="processing",
                        stage="semantic_matching",
                        progress=60,
                        error_message="",
                        frame_count=len(frames),
                        stage_processed=0,
                        stage_total=0,
                        stage_message="",
                    )
        except Exception as exc:
            repo.update_job(
                video_id,
                status="failed",
                stage="failed",
                progress=100,
                error_message=f"{exc}\n{traceback.format_exc(limit=4)}",
                completed=True,
            )
            raise


def parse_wireframes(video_id: str, *, wireframe_job_id: str | None = None, finalize: bool = True) -> None:
    with SessionLocal() as session:
        repo = VideoJobRepository(session)
        job = repo.get_job(video_id)
        if job is None:
            return
        try:
            repo.update_job(video_id, status="processing", stage="generating_wireframes", progress=52, error_message="")
            wireframe_job = get_wireframe_job(wireframe_job_id) if wireframe_job_id else None
            if wireframe_job is None:
                wireframe_job = create_wireframe_job(video_id)
            run_wireframe_job(wireframe_job.id)
            completed_job = get_wireframe_job(wireframe_job.id)
            if completed_job is not None and completed_job.status == "failed":
                raise RuntimeError(completed_job.error_message or "线框图生成失败")
            if finalize:
                repo.update_job(
                    video_id,
                    status="completed",
                    stage="completed",
                    progress=100,
                    error_message="",
                    completed=True,
                )
            else:
                repo.update_job(
                    video_id,
                    status="processing",
                    stage="generating_wireframes",
                    progress=60,
                    error_message="",
                )
        except Exception as exc:
            repo.update_job(
                video_id,
                status="failed",
                stage="failed",
                progress=100,
                error_message=f"{exc}\n{traceback.format_exc(limit=4)}",
                completed=True,
            )
            raise


def parse_transcript(video_id: str, *, finalize: bool = True) -> None:
    with SessionLocal() as session:
        repo = VideoJobRepository(session)
        job = repo.get_job(video_id)
        if job is None:
            return
        try:
            Path(settings.video_workdir).mkdir(parents=True, exist_ok=True)
            repo.update_job(
                video_id,
                status="processing",
                stage="transcribing_asr",
                progress=62,
                error_message="正在调用本地 ASR 模型进行原始转写",
            )
            with tempfile.TemporaryDirectory(prefix=f"{video_id}_parse_asr_", dir=settings.video_workdir) as tmp:
                source_path = Path(tmp) / f"source{Path(job.filename).suffix or '.mp4'}"
                analysis_path, job = ensure_analysis_video_file(
                    repo=repo,
                    job=job,
                    source_path=source_path,
                    output_path=Path(tmp) / "analysis_720p_h265.mp4",
                )
                duration_ms = job.duration_ms or probe_video_duration_ms(analysis_path)
                if duration_ms and duration_ms != job.duration_ms:
                    job = repo.update_job(video_id, duration_ms=duration_ms)
                result = transcribe_or_fallback(
                    analysis_path,
                    job.filename,
                    job.analysis_video_object_key or job.source_object_key,
                    [],
                )
                repo.update_job(
                    video_id,
                    status="processing",
                    stage="structuring_transcript",
                    progress=76,
                    error_message="正在调用 qwen3.7-plus 生成语义结构化转写",
                )
                tree_key = transcript_tree_object_key(video_id)
                rendered_key = transcript_rendered_object_key(video_id)
                structured = build_structured_transcript(
                    job=job,
                    raw_response=result.get("raw_response") or {},
                    frames=[],
                    wireframes=[],
                    duration_ms=duration_ms,
                )
                structured.tree.setdefault("source", {})
                structured.tree["source"]["tree_object_key"] = tree_key
                structured.tree["source"]["rendered_object_key"] = rendered_key
                rendered_text = render_transcript_tree_text(structured.tree)
                storage_service.upload_bytes(
                    object_key=tree_key,
                    content=json.dumps(structured.tree, ensure_ascii=False, indent=2).encode("utf-8"),
                    media_type="application/json; charset=utf-8",
                    bucket=job.source_bucket,
                )
                storage_service.upload_bytes(
                    object_key=rendered_key,
                    content=rendered_text.encode("utf-8"),
                    media_type="text/plain; charset=utf-8",
                    bucket=job.source_bucket,
                )
                if finalize:
                    repo.update_job(
                        video_id,
                        status="completed",
                        stage="completed",
                        progress=100,
                        error_message="",
                        transcript_object_key=rendered_key,
                        completed=True,
                    )
                else:
                    repo.update_job(
                        video_id,
                        status="processing",
                        stage="structuring_transcript",
                        progress=82,
                        error_message="",
                        transcript_object_key=rendered_key,
                    )
        except Exception as exc:
            repo.update_job(
                video_id,
                status="failed",
                stage="failed",
                progress=100,
                error_message=f"{exc}\n{traceback.format_exc(limit=4)}",
                completed=True,
            )
            raise


def parse_markdown(video_id: str, *, finalize: bool = True) -> None:
    with SessionLocal() as session:
        repo = VideoJobRepository(session)
        job = repo.get_job(video_id)
        if job is None:
            return
        try:
            if not job.transcript_object_key:
                raise RuntimeError("视频还没有转写文本，请先生成转写文本。")
            tree_key = transcript_tree_object_key(video_id)
            repo.update_job(
                video_id,
                status="processing",
                stage="generating_markdown",
                progress=88,
                error_message="正在根据结构化转写生成视频分析 Markdown",
            )
            try:
                tree = json.loads(
                    storage_service.get_bytes(bucket=job.source_bucket, object_key=tree_key).decode(
                        "utf-8", errors="replace"
                    )
                )
            except Exception as exc:
                raise RuntimeError("视频还没有新版结构化转写结果，请先重新生成转写文本。") from exc
            frames = repo.frames_for_video(video_id)
            wireframes = latest_wireframes_for_video(session, video_id, frames=frames, bucket=job.source_bucket)
            tree = attach_media_to_transcript_tree(
                tree=tree,
                video_id=video_id,
                frames=frames,
                wireframes=wireframes,
            )
            storage_service.upload_bytes(
                object_key=tree_key,
                content=json.dumps(tree, ensure_ascii=False, indent=2).encode("utf-8"),
                media_type="application/json; charset=utf-8",
                bucket=job.source_bucket,
            )
            rendered_key = job.transcript_object_key or transcript_rendered_object_key(video_id)
            storage_service.upload_bytes(
                object_key=rendered_key,
                content=render_transcript_tree_text(tree).encode("utf-8"),
                media_type="text/plain; charset=utf-8",
                bucket=job.source_bucket,
            )
            markdown_key = markdown_object_key(video_id)
            markdown = build_markdown_from_transcript_tree(
                tree=tree,
                source_video_object=job.source_object_key,
            )
            storage_service.upload_bytes(
                object_key=markdown_key,
                content=markdown.encode("utf-8"),
                media_type="text/markdown; charset=utf-8",
                bucket=job.source_bucket,
            )
            if finalize:
                repo.update_job(
                    video_id,
                    status="completed",
                    stage="completed",
                    progress=100,
                    error_message="",
                    markdown_object_key=markdown_key,
                    completed=True,
                )
            else:
                repo.update_job(
                    video_id,
                    status="processing",
                    stage="generating_markdown",
                    progress=94,
                    error_message="",
                    markdown_object_key=markdown_key,
                )
        except Exception as exc:
            repo.update_job(
                video_id,
                status="failed",
                stage="failed",
                progress=100,
                error_message=f"{exc}\n{traceback.format_exc(limit=4)}",
                completed=True,
            )
            raise


def parse_full(video_id: str) -> None:
    try:
        parse_transcript(video_id, finalize=False)
        parse_keyframes(video_id, finalize=False)
        parse_wireframes(video_id, finalize=False)
        parse_markdown(video_id, finalize=True)
    except Exception:
        raise


def format_timestamp(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def latest_wireframes_for_video(
    session,
    video_id: str,
    *,
    frames: list[VideoFrame] | None = None,
    bucket: str | None = None,
) -> list[dict]:
    frame_rows = frames if frames is not None else VideoJobRepository(session).frames_for_video(video_id)
    return generated_wireframe_refs(
        frames=frame_rows,
        bucket=bucket,
        wireframe_job=latest_wireframe_job(video_id),
    )
