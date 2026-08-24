from __future__ import annotations

from pathlib import Path
from typing import Any

from app.models.standard_library import VideoFrame, WireframeJob
from app.services.storage import storage_service


def transcript_tree_object_key(video_id: str) -> str:
    return f"videos/{video_id}/transcript/transcript_tree.json"


def transcript_raw_object_key(video_id: str) -> str:
    return f"videos/{video_id}/transcript/transcript_raw.json"


def transcript_rendered_object_key(video_id: str) -> str:
    return f"videos/{video_id}/transcript/transcript_rendered.txt"


def frame_quality_report_object_key(video_id: str) -> str:
    return f"videos/{video_id}/frames/frame_quality_report.json"


def frame_dedup_report_object_key(video_id: str) -> str:
    return f"videos/{video_id}/frames/frame_dedup_report.json"


def semantic_frame_matches_object_key(video_id: str) -> str:
    return f"videos/{video_id}/frames/semantic_frame_matches.json"


def analysis_proxy_video_object_key(video_id: str) -> str:
    return f"videos/{video_id}/derived/analysis_720p_h265.mp4"


def markdown_object_key(video_id: str) -> str:
    return f"videos/{video_id}/markdown/result.md"


def wireframe_object_key_for_frame(frame: VideoFrame) -> str:
    frame_stem = Path(frame.object_key.rsplit("/", 1)[-1]).stem or str(frame.id)
    return f"videos/{frame.video_id}/wireframes/{frame_stem}.png"


def wireframe_ref_for_frame(frame: VideoFrame, *, bucket: str | None = None) -> dict[str, Any]:
    object_key = wireframe_object_key_for_frame(frame)
    return {
        "id": frame.id,
        "kind": "wireframe",
        "video_id": frame.video_id,
        "frame_id": frame.id,
        "bucket": bucket or frame.bucket,
        "object_key": object_key,
        "url": storage_service.url_for(object_key),
        "media_type": "image/png",
        "size_bytes": 0,
        "created_at": None,
        "source_frame_object_key": frame.object_key,
    }


def selected_wireframe_frames(frames: list[VideoFrame]) -> list[VideoFrame]:
    return [
        frame
        for frame in frames
        if bool(frame.selected_for_wireframe) and (frame.selection_status or "") == "selected"
    ]


def generated_wireframe_refs(
    *,
    frames: list[VideoFrame],
    bucket: str | None,
    wireframe_job: WireframeJob | None,
) -> list[dict[str, Any]]:
    selected = selected_wireframe_frames(frames)
    if wireframe_job is None:
        return []
    completed = max(0, min(len(selected), int(wireframe_job.completed_frames or 0)))
    if wireframe_job.status == "completed":
        completed = len(selected) if completed <= 0 else completed
    return [wireframe_ref_for_frame(frame, bucket=bucket) for frame in selected[:completed]]
