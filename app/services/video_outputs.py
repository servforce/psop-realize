from __future__ import annotations


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
