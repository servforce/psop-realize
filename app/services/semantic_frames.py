from __future__ import annotations

import io
import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from app.core.config import settings
from app.models.standard_library import VideoFrame, VideoJob
from app.services.frame_selection import analyze_image_quality, mark_frame_rejected, mark_frame_selected
from app.services.query_graph import QueryGraphError, normalize_graph_id, normalize_query_graph, query_graph_prompt_terms


SECTION_FRAME_PADDING_SECONDS = 2.0


@dataclass
class FrameFeature:
    frame: VideoFrame
    path: Path
    quality: dict[str, Any]
    quality_score: float
    hsv_histogram: list[float] | None = None
    phash: int | None = None
    graph_index: dict[str, Any] | None = None
    graph_index_by_prompt: dict[tuple[str, ...], dict[str, Any]] | None = None


@dataclass
class DedupGroup:
    id: str
    items: list[FrameFeature]
    representative: FrameFeature


@dataclass
class SectionFilterResult:
    candidates_by_section: dict[int, list[FrameFeature]]
    candidates_by_operation: dict[tuple[int, int], list[FrameFeature]]
    raw_statuses_by_section: dict[int, dict[int, dict[str, Any]]]
    quality_by_frame_id: dict[int, dict[str, Any]]


@dataclass
class FrameMatchDetail:
    score: float
    matched: list[str]
    missing: list[str]
    negative_hits: list[str]
    objects: list[dict[str, Any]]
    relations: list[dict[str, Any]]
    signals: dict[str, Any]
    quality: dict[str, Any]
    bbox_image_base64: str = ""
    mask_image_base64: str = ""
    mask_available: bool = False
    backend: str = ""
    devices: dict[str, str] | None = None


FRAME_TYPE_KEYWORDS = {
    "wiring_frame": ("接线", "正极", "负极", "信号", "线"),
    "operation_frame": ("安装", "拧", "固定", "插", "按压", "工具", "螺丝"),
    "completion_frame": ("完成", "结束", "结果", "测试", "确认", "检查"),
    "alignment_frame": ("对齐", "孔位", "位置", "连接", "卡住", "预留"),
    "overview_frame": ("介绍", "展示", "组件", "零件", "工具", "材料"),
}

TERM_SYNONYMS = {
    "手部": "hand",
    "手": "hand",
    "工具": "tool",
    "tool": "tool",
    "螺丝刀": "screwdriver",
    "十字螺丝刀": "screwdriver",
    "六角螺丝刀": "screwdriver",
    "screwdriver": "screwdriver",
    "舵机": "servo",
    "servo": "servo",
    "底座": "robot_arm_base",
    "基座": "robot_arm_base",
    "底盘": "robot_arm_base",
    "机械臂底座": "robot_arm_base",
    "base": "robot_arm_base",
    "robot arm base": "robot_arm_base",
    "robot_arm_base": "robot_arm_base",
    "大臂": "upper_arm_link",
    "上臂": "upper_arm_link",
    "upper arm link": "upper_arm_link",
    "upper_arm_link": "upper_arm_link",
    "小臂": "lower_arm_link",
    "前臂": "lower_arm_link",
    "lower arm link": "lower_arm_link",
    "lower_arm_link": "lower_arm_link",
    "机械臂": "arm",
    "arm": "arm",
    "线": "wire",
    "电线": "wire",
    "wire": "wire",
    "控制板": "control board",
    "屏幕": "screen",
    "孔位": "screw hole",
    "螺丝孔": "screw hole",
    "螺丝": "screw",
    "screw": "screw",
    "抓手": "gripper_assembly",
    "夹手": "gripper_assembly",
    "gripper": "gripper_assembly",
    "gripper assembly": "gripper_assembly",
    "gripper_assembly": "gripper_assembly",
    "手腕": "wrist_joint",
    "腕关节": "wrist_joint",
    "wrist joint": "wrist_joint",
    "wrist_joint": "wrist_joint",
    "夹爪": "gripper_assembly",
    "正极": "positive terminal",
    "负极": "negative terminal",
    "+": "positive terminal",
    "-": "negative terminal",
    "信号": "signal",
    "s": "signal",
}

DEFAULT_GRAPH_INDEX_SIGNALS = {
    "clear_key_region",
    "close_up",
    "sharp",
    "not_blurry",
    "detail_rich",
}

def normalize_prompt_terms(terms: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for term in terms or []:
        text = " ".join(str(term or "").split()).strip().lower()
        if not text or text in seen:
            continue
        normalized.append(text)
        seen.add(text)
    return tuple(normalized)


def build_semantic_frame_match_report(
    *,
    job: VideoJob,
    frames: list[VideoFrame],
    frame_paths_by_object_key: dict[str, Path],
    transcript_tree: dict[str, Any],
    progress_callback: Callable[[str, int, int, str], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    sections = extract_sections(transcript_tree)
    if not sections:
        raise RuntimeError("结构化转写中没有可用于语义匹配的文本段落，请先重新生成转写文本。")

    quality_report, dedup_report, filter_result = build_frame_filter_report(
        job=job,
        frames=frames,
        frame_paths_by_object_key=frame_paths_by_object_key,
        sections=sections,
        progress_callback=progress_callback,
    )
    candidates = unique_candidates(filter_result.candidates_by_operation)
    candidate_ids = {int(item.frame.id) for item in candidates}

    if not candidates:
        mark_non_candidate_frames_rejected(
            frames=frames,
            candidate_ids=candidate_ids,
            raw_statuses_by_section=filter_result.raw_statuses_by_section,
            quality_by_frame_id=filter_result.quality_by_frame_id,
        )
        return (
            build_report(
                job=job,
                sections=sections,
                frames=frames,
                candidates_by_section=filter_result.candidates_by_section,
                candidates_by_operation=filter_result.candidates_by_operation,
                raw_statuses_by_section=filter_result.raw_statuses_by_section,
                similarities={},
            ),
            quality_report,
            dedup_report,
        )
    match_details = compute_section_frame_match_details_finetuned(
        sections=sections,
        candidates_by_operation=filter_result.candidates_by_operation,
        progress_callback=progress_callback,
    )
    similarities = {key: value.score for key, value in match_details.items()}
    best_by_frame: dict[int, tuple[dict[str, Any], float]] = {}
    for section in sections:
        section_index = int(section["index"])
        section_candidates = unique_candidates(
            {
                operation_key: operation_candidates
                for operation_key, operation_candidates in filter_result.candidates_by_operation.items()
                if operation_key[0] == section_index
            }
        )
        for item in section_candidates:
            score = best_section_frame_similarity(
                section=section,
                frame_id=int(item.frame.id),
                similarities=similarities,
            )
            current = best_by_frame.get(int(item.frame.id))
            if current is None or score > current[1]:
                best_by_frame[int(item.frame.id)] = (section, score)

    for item in candidates:
        section, score = best_by_frame.get(int(item.frame.id), ({}, 0.0))
        section_index = parse_optional_int(section.get("index"))
        mark_frame_selected(
            item.frame,
            score=score,
            reason="Semantic frame candidate scored by graph index matching.",
            details={
                "stage": "graph_index_matching",
                "graph_index_backend": "finetuned_yolo_world_sam",
                "best_section_index": section_index,
                "best_section_title": section.get("title") or "",
                "best_score": score,
                "dedup_status": "kept",
                "section_padding_seconds": SECTION_FRAME_PADDING_SECONDS,
                "quality": item.quality,
            },
            matched_section_index=section_index,
        )

    mark_non_candidate_frames_rejected(
        frames=frames,
        candidate_ids=candidate_ids,
        raw_statuses_by_section=filter_result.raw_statuses_by_section,
        quality_by_frame_id=filter_result.quality_by_frame_id,
    )

    return (
        build_report(
            job=job,
            sections=sections,
            frames=frames,
            candidates_by_section=filter_result.candidates_by_section,
            candidates_by_operation=filter_result.candidates_by_operation,
            raw_statuses_by_section=filter_result.raw_statuses_by_section,
            similarities=similarities,
            match_details=match_details,
        ),
        quality_report,
        dedup_report,
    )


def build_frame_filter_report(
    *,
    job: VideoJob,
    frames: list[VideoFrame],
    frame_paths_by_object_key: dict[str, Path],
    sections: list[dict[str, Any]],
    progress_callback: Callable[[str, int, int, str], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], SectionFilterResult]:
    quality_report = {"video_id": job.id, "frames": []}
    dedup_report = {
        "video_id": job.id,
        "section_padding_seconds": SECTION_FRAME_PADDING_SECONDS,
        "groups": [],
        "frames": [],
    }

    operation_frames_by_key: dict[tuple[int, int], list[VideoFrame]] = {}
    relevant_frame_ids: set[int] = set()
    for section in sections:
        section_index = int(section["index"])
        operation_index = 1
        operation_frames = frames_for_section(frames, section, padding_seconds=SECTION_FRAME_PADDING_SECONDS)
        operation_frames_by_key[(section_index, operation_index)] = operation_frames
        relevant_frame_ids.update(int(frame.id) for frame in operation_frames)

    frames_to_evaluate = [frame for frame in frames if int(frame.id) in relevant_frame_ids]
    feature_by_frame_id: dict[int, FrameFeature] = {}
    quality_by_frame_id: dict[int, dict[str, Any]] = {}
    total_frames = len(frames_to_evaluate)
    notify_progress(progress_callback, "filtering_frames", 0, total_frames, "段落时间窗内图像质量过滤")
    for index, frame in enumerate(frames_to_evaluate, start=1):
        frame_id = int(frame.id)
        path = frame_paths_by_object_key.get(frame.object_key)
        if path is None or not path.exists():
            quality_by_frame_id[frame_id] = {
                "passed": False,
                "reason": "Frame file is unavailable for semantic matching.",
                "details": {"missing_local_file": True},
            }
            quality_report["frames"].append(
                {
                    "frame_id": frame.id,
                    "timestamp_seconds": frame.timestamp_seconds,
                    "status": "quality_rejected",
                    "reason": "Frame file is unavailable for semantic matching.",
                    "details": {"missing_local_file": True},
                }
            )
            notify_progress(progress_callback, "filtering_frames", index, total_frames, "段落时间窗内图像质量过滤")
            continue

        image_bytes = path.read_bytes()
        quality = analyze_image_quality(image_bytes)
        quality_by_frame_id[frame_id] = {
            "passed": quality.passed,
            "reason": quality.reason,
            "details": quality.details,
        }
        quality_report["frames"].append(
            {
                "frame_id": frame.id,
                "timestamp_seconds": frame.timestamp_seconds,
                "status": "quality_passed" if quality.passed else "quality_rejected",
                "reason": quality.reason,
                "details": quality.details,
            }
        )
        if not quality.passed:
            notify_progress(progress_callback, "filtering_frames", index, total_frames, "段落时间窗内图像质量过滤")
            continue

        feature_by_frame_id[frame_id] = FrameFeature(
            frame=frame,
            path=path,
            quality=quality.details,
            quality_score=quality_score(quality.details),
        )
        notify_progress(progress_callback, "filtering_frames", index, total_frames, "段落时间窗内图像质量过滤")

    candidates_by_section: dict[int, list[FrameFeature]] = {}
    candidates_by_operation: dict[tuple[int, int], list[FrameFeature]] = {}
    raw_statuses_by_section: dict[int, dict[int, dict[str, Any]]] = {}
    for section in sections:
        section_index = int(section["index"])
        raw_frames = frames_for_section(frames, section, padding_seconds=SECTION_FRAME_PADDING_SECONDS)
        raw_statuses: dict[int, dict[str, Any]] = {}
        raw_statuses_by_section[section_index] = raw_statuses
        for frame in raw_frames:
            frame_id = int(frame.id)
            quality_item = quality_by_frame_id.get(frame_id, {})
            if quality_item and not bool(quality_item.get("passed")):
                raw_statuses[frame_id] = {
                    "filter_status": "quality_rejected",
                    "filter_label": "质量不通过",
                    "filter_reason": quality_item.get("reason") or "Frame failed quality filtering.",
                }

        for operation in section_query_operations(section):
            operation_index = 1
            operation_features = [
                feature_by_frame_id[int(frame.id)]
                for frame in operation_frames_by_key.get((section_index, operation_index), [])
                if int(frame.id) in feature_by_frame_id
            ]
            operation_groups = deduplicate_frames(operation_features, progress_callback=progress_callback)
            operation_key = (section_index, operation_index)
            candidates_by_operation[operation_key] = sorted(
                [group.representative for group in operation_groups],
                key=lambda item: (int(item.frame.timestamp_ms or 0), int(item.frame.id or 0)),
            )
            for group in operation_groups:
                dedup_group_id = f"section_{section_index}_query_graph_{group.id}"
                dedup_report["groups"].append(
                    {
                        "section_index": section_index,
                        "query_graph_index": operation_index,
                        "dedup_group_id": dedup_group_id,
                        "representative_frame_id": group.representative.frame.id,
                        "group_size": len(group.items),
                        "frame_ids": [item.frame.id for item in group.items],
                    }
                )
                for item in group.items:
                    dedup_report["frames"].append(
                        {
                            "section_index": section_index,
                            "query_graph_index": operation_index,
                            "frame_id": item.frame.id,
                            "dedup_status": "kept"
                            if item.frame.id == group.representative.frame.id
                            else "duplicate_rejected",
                            "dedup_group_id": dedup_group_id,
                            "duplicate_of_frame_id": None
                            if item.frame.id == group.representative.frame.id
                            else group.representative.frame.id,
                            "quality_score": item.quality_score,
                        }
                    )

            for group in operation_groups:
                for item in group.items:
                    if item.frame.id == group.representative.frame.id:
                        continue
                    raw_statuses[int(item.frame.id)] = {
                        "filter_status": "duplicate_rejected",
                        "filter_label": "重复",
                        "filter_reason": "Rejected as a duplicate frame in this transcript section window.",
                        "dedup_group_id": f"section_{section_index}_query_graph_{group.id}",
                        "duplicate_of_frame_id": group.representative.frame.id,
                    }

        section_candidate_ids = {
            int(item.frame.id)
            for operation_key, operation_candidates in candidates_by_operation.items()
            if operation_key[0] == section_index
            for item in operation_candidates
        }
        candidates_by_section[section_index] = [
            feature_by_frame_id[frame_id]
            for frame_id in sorted(
                section_candidate_ids,
                key=lambda value: (
                    int(feature_by_frame_id[value].frame.timestamp_ms or 0),
                    int(feature_by_frame_id[value].frame.id or 0),
                ),
            )
            if frame_id in feature_by_frame_id
        ]

    return (
        quality_report,
        dedup_report,
        SectionFilterResult(
            candidates_by_section=candidates_by_section,
            candidates_by_operation=candidates_by_operation,
            raw_statuses_by_section=raw_statuses_by_section,
            quality_by_frame_id=quality_by_frame_id,
        ),
    )


def unique_candidates(candidates_by_section: dict[int, list[FrameFeature]]) -> list[FrameFeature]:
    by_id: dict[int, FrameFeature] = {}
    for candidates in candidates_by_section.values():
        for item in candidates:
            by_id.setdefault(int(item.frame.id), item)
    return sorted(by_id.values(), key=lambda item: (int(item.frame.timestamp_ms or 0), int(item.frame.id or 0)))


def mark_non_candidate_frames_rejected(
    *,
    frames: list[VideoFrame],
    candidate_ids: set[int],
    raw_statuses_by_section: dict[int, dict[int, dict[str, Any]]],
    quality_by_frame_id: dict[int, dict[str, Any]],
) -> None:
    statuses_by_frame: dict[int, list[dict[str, Any]]] = {}
    for section_index, raw_statuses in raw_statuses_by_section.items():
        for frame_id, status in raw_statuses.items():
            statuses_by_frame.setdefault(frame_id, []).append({"section_index": section_index, **status})

    for frame in frames:
        frame_id = int(frame.id)
        if frame_id in candidate_ids:
            continue

        if frame_id not in quality_by_frame_id:
            mark_frame_rejected(
                frame,
                score=0.0,
                reason="Frame is outside all transcript section windows.",
                details={
                    "stage": "section_window",
                    "section_padding_seconds": SECTION_FRAME_PADDING_SECONDS,
                },
                matched_section_index=None,
            )
            continue

        quality_item = quality_by_frame_id.get(frame_id, {})
        if not bool(quality_item.get("passed")):
            mark_frame_rejected(
                frame,
                score=0.0,
                reason=str(quality_item.get("reason") or "Frame failed quality filtering."),
                details={
                    "stage": "quality_filter",
                    "quality": quality_item.get("details") or {},
                },
                matched_section_index=None,
            )
            continue

        duplicate_statuses = [
            status
            for status in statuses_by_frame.get(frame_id, [])
            if status.get("filter_status") == "duplicate_rejected"
        ]
        if duplicate_statuses:
            mark_frame_rejected(
                frame,
                score=0.0,
                reason="Rejected as a duplicate frame in transcript section windows.",
                details={
                    "stage": "section_dedup",
                    "section_statuses": duplicate_statuses,
                },
                matched_section_index=None,
            )
            continue

        mark_frame_rejected(
            frame,
            score=0.0,
            reason="Frame is outside all transcript section windows.",
            details={
                "stage": "section_window",
                "section_padding_seconds": SECTION_FRAME_PADDING_SECONDS,
            },
            matched_section_index=None,
        )


def extract_sections(tree: dict[str, Any]) -> list[dict[str, Any]]:
    raw_sections = ((tree.get("tree") or {}).get("sections") or []) if isinstance(tree, dict) else []
    sections = []
    for index, section in enumerate(raw_sections, start=1):
        if not isinstance(section, dict):
            continue
        section_index = parse_optional_int(section.get("index")) or index
        title = str(section.get("title") or f"段落 {section_index}").strip()
        text = str(section.get("text") or "").strip()
        polished_text = str(section.get("polished_text") or "").strip()
        business_frame_text = str(section.get("business_frame_text") or "").strip()
        query_graph = extract_section_query_graph(section, section_index=section_index, title=title)
        sections.append(
            {
                "index": section_index,
                "title": title,
                "text": text,
                "polished_text": polished_text,
                "business_frame_text": business_frame_text,
                "query_graph": query_graph,
                "query_graph_terms": query_graph_prompt_terms(query_graph),
                "graph_query_text": business_frame_text,
                "start_seconds": parse_float(section.get("start_seconds")),
                "end_seconds": parse_float(section.get("end_seconds")),
                "start_time": section.get("start_time"),
                "end_time": section.get("end_time"),
            }
        )
    return sections


def extract_section_query_graph(section: dict[str, Any], *, section_index: int, title: str) -> dict[str, Any]:
    raw_graph = section.get("query_graph")
    try:
        return normalize_query_graph(raw_graph)
    except QueryGraphError as exc:
        raise RuntimeError(f"{exc}: section_index={section_index}, section_title={title!r}") from exc


def deduplicate_frames(
    items: list[FrameFeature],
    *,
    progress_callback: Callable[[str, int, int, str], None] | None = None,
) -> list[DedupGroup]:
    groups: list[DedupGroup] = []
    window_seconds = max(0.0, float(settings.video_dedup_time_window_seconds or 0))
    sorted_items = sorted(items, key=lambda value: value.frame.timestamp_ms or 0)
    total_items = len(sorted_items)
    for index, item in enumerate(sorted_items, start=1):
        notify_progress(progress_callback, "deduplicating_frames", index, total_items, "HSV+pHash 去重")
        matched: DedupGroup | None = None
        for group in reversed(groups):
            delta = abs(float(item.frame.timestamp_seconds or 0) - float(group.representative.frame.timestamp_seconds or 0))
            if delta > window_seconds:
                continue
            if is_duplicate(item, group.representative):
                matched = group
                break
        if matched is None:
            groups.append(DedupGroup(id=f"dedup_group_{len(groups) + 1:04d}", items=[item], representative=item))
            continue
        matched.items.append(item)
        if item.quality_score > matched.representative.quality_score:
            matched.representative = item
    return groups


def is_duplicate(left: FrameFeature, right: FrameFeature) -> bool:
    ensure_dedup_features(left)
    ensure_dedup_features(right)
    color_similarity = histogram_similarity(left.hsv_histogram or [], right.hsv_histogram or [])
    phash_distance = hamming_distance(int(left.phash or 0), int(right.phash or 0))
    return (
        color_similarity >= float(settings.video_dedup_hsv_similarity_threshold)
        and phash_distance <= int(settings.video_dedup_phash_distance_threshold)
    )


def ensure_dedup_features(item: FrameFeature) -> None:
    if item.hsv_histogram is not None and item.phash is not None:
        return
    image_bytes = item.path.read_bytes()
    item.hsv_histogram = hsv_histogram(image_bytes)
    item.phash = phash(image_bytes)


def hsv_histogram(image_bytes: bytes) -> list[float]:
    import cv2
    import numpy as np

    buffer = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("OpenCV failed to decode image")
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    histogram = cv2.calcHist([hsv], [0, 1, 2], None, [8, 4, 4], [0, 180, 0, 256, 0, 256])
    histogram = cv2.normalize(histogram, histogram).flatten()
    return [float(value) for value in histogram]


def histogram_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    mean_left = sum(left) / len(left)
    mean_right = sum(right) / len(right)
    numerator = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right))
    denom_left = math.sqrt(sum((a - mean_left) ** 2 for a in left))
    denom_right = math.sqrt(sum((b - mean_right) ** 2 for b in right))
    denom = denom_left * denom_right
    if denom <= 0:
        return 0.0
    return max(-1.0, min(1.0, numerator / denom))


def phash(image_bytes: bytes) -> int:
    import cv2
    import numpy as np
    from PIL import Image

    with Image.open(io.BytesIO(image_bytes)) as image:
        gray = image.convert("L").resize((32, 32))
        pixels = np.asarray(gray, dtype=np.float32)
    dct = cv2.dct(pixels)
    block = dct[:8, :8].flatten()
    median = float(np.median(block[1:])) if len(block) > 1 else float(np.median(block))
    value = 0
    for bit_index, item in enumerate(block):
        if float(item) > median:
            value |= 1 << bit_index
    return value


def hamming_distance(left: int, right: int) -> int:
    return int(left ^ right).bit_count()


def quality_score(details: dict[str, Any]) -> float:
    blur = max(0.0, min(1.0, float(details.get("blur_score") or 0.0) / 200.0))
    brightness = float(details.get("mean_brightness") or 0.0)
    brightness_score = max(0.0, 1.0 - abs(brightness - 128.0) / 128.0)
    exposure_score = 1.0 - max(float(details.get("dark_ratio") or 0.0), float(details.get("bright_ratio") or 0.0))
    return max(0.0, min(1.0, blur * 0.6 + brightness_score * 0.3 + exposure_score * 0.1))


def image_size_from_quality(quality: dict[str, Any]) -> dict[str, int]:
    return {
        "width": max(1, int(quality.get("width") or 0)),
        "height": max(1, int(quality.get("height") or 0)),
    }


@lru_cache(maxsize=4)
def load_mobile_sam_model(model_name: str, *, device: str) -> Any:
    try:
        from ultralytics import SAM
    except ImportError as exc:  # pragma: no cover - exercised only in a missing-dependency environment
        raise RuntimeError(
            "ultralytics is required for YOLO-World + MobileSAM graph indexing. "
            "Install the project dependencies first."
        ) from exc

    model = SAM(model_name)
    if hasattr(model, "to"):
        try:
            model.to(device)
        except Exception:
            pass
    return model


@lru_cache(maxsize=4)
def load_yolo_world_model(model_name: str) -> Any:
    try:
        from ultralytics import YOLOWorld

        return YOLOWorld(model_name)
    except Exception:
        from ultralytics import YOLO

        return YOLO(model_name)


def load_image_metadata(image_path: Path) -> dict[str, Any]:
    from PIL import Image

    with Image.open(image_path) as image:
        width, height = image.size
        mode = image.mode
    return {
        "size": {"width": int(width), "height": int(height)},
        "mode": mode,
        "area": int(width * height),
    }


def detect_prompt_objects(
    *,
    model: Any,
    image_path: Path,
    prompt_terms: list[str],
    confidence: float,
    iou: float,
    max_det: int,
    device: str,
) -> dict[str, Any]:
    classes = [term for term in (prompt_terms or []) if term]

    results = model.predict(
        source=str(image_path),
        conf=float(confidence),
        iou=float(iou),
        max_det=int(max_det),
        device=device,
        verbose=False,
    )
    result = results[0] if results else None
    names = getattr(result, "names", None) or getattr(model, "names", None) or {}
    boxes = getattr(result, "boxes", None)
    masks = getattr(result, "masks", None)
    mask_data = None
    if masks is not None and getattr(masks, "data", None) is not None:
        mask_data = masks.data
        if hasattr(mask_data, "detach"):
            mask_data = mask_data.detach().cpu().numpy()
        else:
            try:
                mask_data = mask_data.cpu().numpy()
            except Exception:
                mask_data = None
    image_size = getattr(result, "orig_shape", None)
    if isinstance(image_size, tuple) and len(image_size) >= 2:
        height, width = int(image_size[0]), int(image_size[1])
    else:
        metadata = load_image_metadata(image_path)
        width = int(metadata["size"]["width"])
        height = int(metadata["size"]["height"])
    if result is None or boxes is None or len(boxes) == 0:
        return {
            "items": [],
            "classes": classes,
            "image_size": {"width": width, "height": height},
        }

    items: list[dict[str, Any]] = []
    for index in range(len(boxes)):
        box = boxes[index]
        cls_index = int(float(box.cls.item() if hasattr(box.cls, "item") else box.cls))
        if isinstance(names, dict):
            raw_label = names.get(cls_index, f"class_{cls_index}")
        elif isinstance(names, list) and 0 <= cls_index < len(names):
            raw_label = names[cls_index]
        else:
            raw_label = f"class_{cls_index}"
        label = normalize_graph_label(raw_label)
        xyxy = box.xyxy[0].tolist() if hasattr(box.xyxy, "__getitem__") else list(box.xyxy.tolist())
        mask_bbox = xyxy[:4]
        bbox_area = bbox_area_ratio_for_box(mask_bbox, width=width, height=height)
        mask_area_ratio = bbox_area
        centroid_x, centroid_y = bbox_centroid(mask_bbox)
        touches_border = bbox_touches_border(mask_bbox, width=width, height=height)
        if mask_data is not None and index < len(mask_data):
            mask_metrics = metrics_from_mask(mask_data[index], width=width, height=height)
            mask_bbox = mask_metrics["bbox"]
            mask_area_ratio = mask_metrics["area_ratio"]
            centroid_x = mask_metrics["centroid_x"]
            centroid_y = mask_metrics["centroid_y"]
            touches_border = bool(mask_metrics["touches_border"])
        items.append(
            {
                "id": f"det_{index + 1}",
                "label": label,
                "confidence": float(box.conf.item() if hasattr(box.conf, "item") else box.conf),
                "bbox": [float(value) for value in xyxy[:4]],
                "mask_bbox": [float(value) for value in mask_bbox[:4]],
                "bbox_area_ratio": float(bbox_area),
                "mask_area_ratio": float(mask_area_ratio),
                "centroid_x": float(centroid_x),
                "centroid_y": float(centroid_y),
                "touches_border": bool(touches_border),
                "class_id": cls_index,
            }
        )

    return {
        "items": items,
        "classes": classes,
        "image_size": {"width": width, "height": height},
    }


def build_detection_objects(*, detections: dict[str, Any], image_size: dict[str, int]) -> list[dict[str, Any]]:
    detection_items = detections.get("items") if isinstance(detections, dict) else []
    if not isinstance(detection_items, list) or not detection_items:
        return []
    width = max(1, int(image_size.get("width") or 0))
    height = max(1, int(image_size.get("height") or 0))
    objects: list[dict[str, Any]] = []
    for index, detection in enumerate(detection_items):
        if not isinstance(detection, dict):
            continue
        bbox = detection.get("bbox") if isinstance(detection.get("bbox"), list) else [0.0, 0.0, 0.0, 0.0]
        bbox = [float(value) for value in bbox[:4]]
        mask_bbox = detection.get("mask_bbox") if isinstance(detection.get("mask_bbox"), list) else bbox[:]
        mask_bbox = [float(value) for value in mask_bbox[:4]]
        bbox_area_ratio = float(detection.get("bbox_area_ratio") or bbox_area_ratio_for_box(bbox, width=width, height=height))
        mask_area_ratio = float(detection.get("mask_area_ratio") or bbox_area_ratio)
        centroid_x = float(detection.get("centroid_x")) if detection.get("centroid_x") is not None else bbox_centroid(mask_bbox)[0]
        centroid_y = float(detection.get("centroid_y")) if detection.get("centroid_y") is not None else bbox_centroid(mask_bbox)[1]
        touches_border = bool(detection.get("touches_border")) if detection.get("touches_border") is not None else bbox_touches_border(mask_bbox, width=width, height=height)
        objects.append(
            {
                "id": detection.get("id") or f"obj_{index + 1}",
                "label": normalize_graph_label(detection.get("label")),
                "confidence": float(detection.get("confidence") or 0.0),
                "bbox": bbox,
                "mask_bbox": mask_bbox,
                "bbox_area_ratio": bbox_area_ratio,
                "mask_area_ratio": mask_area_ratio,
                "centroid_x": centroid_x,
                "centroid_y": centroid_y,
                "touches_border": touches_border,
                "class_id": detection.get("class_id"),
            }
        )
    return objects


def segment_prompt_objects(
    *,
    model: Any,
    image_path: Path,
    detections: dict[str, Any],
    image_size: dict[str, int],
    device: str,
) -> list[dict[str, Any]]:
    detection_items = detections.get("items") if isinstance(detections, dict) else []
    if not isinstance(detection_items, list) or not detection_items:
        return []

    bboxes = [item.get("bbox") for item in detection_items if isinstance(item, dict) and isinstance(item.get("bbox"), list)]
    if not bboxes:
        return []

    results = model.predict(
        source=str(image_path),
        bboxes=bboxes,
        device=device,
        retina_masks=True,
        verbose=False,
    )
    result = results[0] if results else None
    masks = getattr(result, "masks", None)
    mask_data = None
    if masks is not None and getattr(masks, "data", None) is not None:
        mask_data = masks.data
        if hasattr(mask_data, "detach"):
            mask_data = mask_data.detach().cpu().numpy()
        else:
            try:
                mask_data = mask_data.cpu().numpy()
            except Exception:
                mask_data = None

    width = max(1, int(image_size.get("width") or 0))
    height = max(1, int(image_size.get("height") or 0))
    image_area = float(width * height)
    objects: list[dict[str, Any]] = []
    for index, detection in enumerate(detection_items):
        if not isinstance(detection, dict):
            continue
        bbox = detection.get("bbox") if isinstance(detection.get("bbox"), list) else [0.0, 0.0, 0.0, 0.0]
        bbox = [float(value) for value in bbox[:4]]
        bbox_area_ratio = bbox_area_ratio_for_box(bbox, width=width, height=height)
        mask_area_ratio = bbox_area_ratio
        centroid_x, centroid_y = bbox_centroid(bbox)
        touches_border = bbox_touches_border(bbox, width=width, height=height)
        mask_bbox = bbox[:]
        if mask_data is not None and index < len(mask_data):
            mask = mask_data[index]
            mask_metrics = metrics_from_mask(mask, width=width, height=height)
            mask_area_ratio = mask_metrics["area_ratio"]
            mask_bbox = mask_metrics["bbox"]
            centroid_x = mask_metrics["centroid_x"]
            centroid_y = mask_metrics["centroid_y"]
            touches_border = bool(mask_metrics["touches_border"])

        objects.append(
            {
                "id": detection.get("id") or f"obj_{index + 1}",
                "label": normalize_graph_label(detection.get("label")),
                "confidence": float(detection.get("confidence") or 0.0),
                "bbox": bbox,
                "mask_bbox": mask_bbox,
                "bbox_area_ratio": bbox_area_ratio,
                "mask_area_ratio": mask_area_ratio,
                "centroid_x": centroid_x,
                "centroid_y": centroid_y,
                "touches_border": touches_border,
                "class_id": detection.get("class_id"),
            }
        )
    return objects


def infer_graph_relations(objects: list[dict[str, Any]], *, image_size: dict[str, int]) -> list[dict[str, Any]]:
    if not objects:
        return []
    width = max(1, int(image_size.get("width") or 0))
    height = max(1, int(image_size.get("height") or 0))
    relations: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for left in objects:
        left_label = normalize_graph_label(left.get("label"))
        if not left_label:
            continue
        left_bbox = left.get("mask_bbox") if isinstance(left.get("mask_bbox"), list) else left.get("bbox")
        for right in objects:
            if left is right:
                continue
            right_label = normalize_graph_label(right.get("label"))
            if not right_label:
                continue
            right_bbox = right.get("mask_bbox") if isinstance(right.get("mask_bbox"), list) else right.get("bbox")
            overlap = bbox_overlap_ratio(left_bbox, right_bbox, width=width, height=height)
            distance = bbox_center_distance(left, right, width=width, height=height)
            relation = None
            score = max(overlap, max(0.0, 1.0 - distance))
            if left_label == "hand" and overlap >= 0.03:
                relation = "occludes"
            elif left_label == "hand" and distance <= 0.28:
                relation = "near"
            elif overlap >= 0.10:
                relation = "overlaps"
            elif distance <= 0.22:
                relation = "near"
            if relation is None:
                continue
            key = (left_label, relation, right_label)
            if key in seen:
                continue
            seen.add(key)
            relations.append({"from": left_label, "relation": relation, "to": right_label, "score": round(float(score), 4)})
            if len(relations) >= 20:
                return relations
    return relations


def infer_graph_signals(
    *,
    quality: dict[str, Any],
    image_size: dict[str, int],
    objects: list[dict[str, Any]],
    relations: list[dict[str, Any]],
) -> dict[str, bool]:
    signals: dict[str, bool] = {signal: False for signal in DEFAULT_GRAPH_INDEX_SIGNALS}
    width = max(1, int(image_size.get("width") or 0))
    height = max(1, int(image_size.get("height") or 0))
    labels = [normalize_graph_label(item.get("label")) for item in objects if normalize_graph_label(item.get("label"))]
    label_set = set(labels)
    for label in label_set:
        signals[f"has_{label.replace(' ', '_')}"] = True

    blur_score = float(quality.get("blur_score") or 0.0)
    sharp = blur_score >= float(settings.video_frame_selection_min_blur_score)
    signals["sharp"] = sharp
    signals["not_blurry"] = sharp

    total_area = 0.0
    largest_area = 0.0
    largest_center_distance = 1.0
    hand_area = 0.0
    for item in objects:
        area_ratio = float(item.get("mask_area_ratio") or item.get("bbox_area_ratio") or 0.0)
        total_area += area_ratio
        largest_area = max(largest_area, area_ratio)
        center_distance = center_distance_from_image_center(item, width=width, height=height)
        if area_ratio >= largest_area:
            largest_center_distance = center_distance
        if normalize_graph_label(item.get("label")) == "hand":
            hand_area += area_ratio

    occluded = any(normalize_graph_label(item.get("relation")) == "occludes" for item in relations)
    if hand_area >= 0.12:
        occluded = True
    signals["occluded"] = occluded
    signals["detail_rich"] = len(objects) >= 2 or total_area >= 0.08
    signals["close_up"] = largest_area >= 0.10
    signals["clear_key_region"] = largest_area >= 0.03 and largest_center_distance <= 0.42 and not occluded
    signals["stable_frame"] = sharp and not occluded
    signals["main_subject_large"] = largest_area >= 0.08
    signals["tiny_subject"] = largest_area > 0.0 and largest_area < 0.02
    signals["has_hand"] = "hand" in label_set
    signals["has_tool"] = "tool" in label_set

    if total_area < 0.01:
        signals["tiny_subject"] = True
    return signals


def bbox_area_ratio_for_box(box: list[float], *, width: int, height: int) -> float:
    if len(box) < 4 or width <= 0 or height <= 0:
        return 0.0
    x1, y1, x2, y2 = [float(value) for value in box[:4]]
    return max(0.0, min(1.0, ((max(0.0, x2 - x1) * max(0.0, y2 - y1)) / float(width * height))))


def bbox_centroid(box: list[float]) -> tuple[float, float]:
    if len(box) < 4:
        return 0.5, 0.5
    x1, y1, x2, y2 = [float(value) for value in box[:4]]
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def bbox_touches_border(box: list[float], *, width: int, height: int, margin_ratio: float = 0.0) -> bool:
    if len(box) < 4 or width <= 0 or height <= 0:
        return False
    x1, y1, x2, y2 = [float(value) for value in box[:4]]
    margin_x = width * float(margin_ratio)
    margin_y = height * float(margin_ratio)
    return x1 <= margin_x or y1 <= margin_y or x2 >= width - margin_x or y2 >= height - margin_y


def bbox_overlap_ratio(left: list[float] | None, right: list[float] | None, *, width: int, height: int) -> float:
    if not left or not right or len(left) < 4 or len(right) < 4 or width <= 0 or height <= 0:
        return 0.0
    x1 = max(float(left[0]), float(right[0]))
    y1 = max(float(left[1]), float(right[1]))
    x2 = min(float(left[2]), float(right[2]))
    y2 = min(float(left[3]), float(right[3]))
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    denom = max(1.0, min((float(left[2]) - float(left[0])) * (float(left[3]) - float(left[1])), (float(right[2]) - float(right[0])) * (float(right[3]) - float(right[1]))))
    return max(0.0, min(1.0, inter / denom))


def bbox_target_overlap_ratio(cover: list[float] | None, target: list[float] | None, *, width: int, height: int) -> float:
    if not cover or not target or len(cover) < 4 or len(target) < 4 or width <= 0 or height <= 0:
        return 0.0
    x1 = max(float(cover[0]), float(target[0]))
    y1 = max(float(cover[1]), float(target[1]))
    x2 = min(float(cover[2]), float(target[2]))
    y2 = min(float(cover[3]), float(target[3]))
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    target_area = max(1.0, (float(target[2]) - float(target[0])) * (float(target[3]) - float(target[1])))
    return clamp_unit(inter / target_area)


def bbox_center_distance(left: dict[str, Any], right: dict[str, Any], *, width: int, height: int) -> float:
    if width <= 0 or height <= 0:
        return 1.0
    left_x = float(left.get("centroid_x") if left.get("centroid_x") is not None else 0.5)
    left_y = float(left.get("centroid_y") if left.get("centroid_y") is not None else 0.5)
    right_x = float(right.get("centroid_x") if right.get("centroid_x") is not None else 0.5)
    right_y = float(right.get("centroid_y") if right.get("centroid_y") is not None else 0.5)
    return math.hypot((left_x - right_x) / float(width), (left_y - right_y) / float(height))


def center_distance_from_image_center(item: dict[str, Any], *, width: int, height: int) -> float:
    if width <= 0 or height <= 0:
        return 1.0
    centroid_x = float(item.get("centroid_x") if item.get("centroid_x") is not None else width / 2.0)
    centroid_y = float(item.get("centroid_y") if item.get("centroid_y") is not None else height / 2.0)
    return math.hypot((centroid_x - (width / 2.0)) / float(width), (centroid_y - (height / 2.0)) / float(height))


def clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def expected_mask_area_ratio(frame_type: str) -> float:
    mapping = {
        "alignment_frame": 0.05,
        "operation_frame": 0.08,
        "wiring_frame": 0.08,
        "completion_frame": 0.10,
        "overview_frame": 0.04,
    }
    return mapping.get(str(frame_type or "").strip(), 0.06)


def graph_object_bbox(item: dict[str, Any]) -> list[float]:
    bbox = item.get("mask_bbox") if isinstance(item.get("mask_bbox"), list) else item.get("bbox")
    if not isinstance(bbox, list):
        return [0.0, 0.0, 0.0, 0.0]
    return [float(value) for value in bbox[:4]]


def graph_object_area_ratio(item: dict[str, Any]) -> float:
    return float(item.get("mask_area_ratio") or item.get("bbox_area_ratio") or 0.0)


def graph_object_quality_score(item: dict[str, Any], *, width: int, height: int, frame_type: str) -> float:
    confidence = clamp_unit(float(item.get("confidence") or 0.0))
    area_ratio = graph_object_area_ratio(item)
    ideal_area = max(0.005, expected_mask_area_ratio(frame_type))
    area_score = clamp_unit(1.0 - abs(area_ratio - ideal_area) / ideal_area)
    center_score = clamp_unit(1.0 - center_distance_from_image_center(item, width=width, height=height) / 0.75)
    border_score = 0.75 if bool(item.get("touches_border")) else 1.0
    return clamp_unit(0.45 * confidence + 0.30 * area_score + 0.15 * center_score + 0.10 * border_score)


def graph_pair_spatial_score(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    width: int,
    height: int,
    relation_keys: set[str],
) -> float:
    left_bbox = graph_object_bbox(left)
    right_bbox = graph_object_bbox(right)
    overlap = bbox_overlap_ratio(left_bbox, right_bbox, width=width, height=height)
    distance = bbox_center_distance(left, right, width=width, height=height)
    proximity = max(overlap, max(0.0, 1.0 - distance))
    left_quality = graph_object_quality_score(left, width=width, height=height, frame_type="operation_frame")
    right_quality = graph_object_quality_score(right, width=width, height=height, frame_type="operation_frame")
    score = 0.65 * proximity + 0.35 * min(left_quality, right_quality)
    left_label = normalize_graph_label(left.get("label"))
    right_label = normalize_graph_label(right.get("label"))
    if left_label and right_label:
        relation_hit = (
            f"{left_label}__near__{right_label}" in relation_keys
            or f"{right_label}__near__{left_label}" in relation_keys
            or f"{left_label}__overlaps__{right_label}" in relation_keys
            or f"{right_label}__overlaps__{left_label}" in relation_keys
        )
        if relation_hit:
            score += 0.10
    return clamp_unit(score)


def graph_visibility_score(
    *,
    matched_objects: list[dict[str, Any]],
    hand_object: dict[str, Any] | None,
    width: int,
    height: int,
    signals: dict[str, bool],
) -> float:
    if not hand_object:
        return 1.0 if not bool(signals.get("occluded")) else 0.85
    hand_bbox = graph_object_bbox(hand_object)
    hand_area = graph_object_area_ratio(hand_object)
    max_overlap_ratio = 0.0
    for item in matched_objects:
        if normalize_graph_label(item.get("label")) == "hand":
            continue
        overlap = bbox_target_overlap_ratio(hand_bbox, graph_object_bbox(item), width=width, height=height)
        max_overlap_ratio = max(max_overlap_ratio, overlap)
    hand_presence = clamp_unit(hand_area / 0.12)
    visibility = 1.0 - 0.75 * max_overlap_ratio - 0.25 * hand_presence
    if bool(signals.get("occluded")):
        visibility = min(visibility, 0.35)
    return clamp_unit(visibility)


def metrics_from_mask(mask: Any, *, width: int, height: int) -> dict[str, Any]:
    import numpy as np

    if mask is None:
        return {
            "area_ratio": 0.0,
            "bbox": [0.0, 0.0, 0.0, 0.0],
            "centroid_x": float(width / 2.0),
            "centroid_y": float(height / 2.0),
            "touches_border": False,
        }
    array = np.asarray(mask)
    if array.ndim > 2:
        array = array.squeeze()
    if array.size == 0:
        return {
            "area_ratio": 0.0,
            "bbox": [0.0, 0.0, 0.0, 0.0],
            "centroid_x": float(width / 2.0),
            "centroid_y": float(height / 2.0),
            "touches_border": False,
        }
    binary = array > 0.5
    ys, xs = np.where(binary)
    if len(xs) == 0 or len(ys) == 0:
        return {
            "area_ratio": 0.0,
            "bbox": [0.0, 0.0, 0.0, 0.0],
            "centroid_x": float(width / 2.0),
            "centroid_y": float(height / 2.0),
            "touches_border": False,
        }
    mask_height, mask_width = binary.shape[:2]
    scale_x = float(width) / max(1.0, float(mask_width))
    scale_y = float(height) / max(1.0, float(mask_height))
    x1 = float(xs.min()) * scale_x
    y1 = float(ys.min()) * scale_y
    x2 = float(xs.max() + 1) * scale_x
    y2 = float(ys.max() + 1) * scale_y
    area_ratio = float(binary.mean())
    centroid_x = float(xs.mean()) * scale_x
    centroid_y = float(ys.mean()) * scale_y
    touches_border = bool(x1 <= 0 or y1 <= 0 or x2 >= width or y2 >= height)
    return {
        "area_ratio": area_ratio,
        "bbox": [x1, y1, x2, y2],
        "centroid_x": centroid_x,
        "centroid_y": centroid_y,
        "touches_border": touches_border,
    }


def image_texture_metrics(image_bytes: bytes) -> dict[str, float]:
    import cv2
    import numpy as np

    buffer = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        return {"edge_density": 0.0, "center_contrast": 0.0}
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 180)
    edge_density = float((edges > 0).mean())
    height, width = gray.shape[:2]
    if height <= 0 or width <= 0:
        return {"edge_density": edge_density, "center_contrast": 0.0}
    margin_h = max(1, height // 4)
    margin_w = max(1, width // 4)
    center = gray[margin_h:-margin_h or None, margin_w:-margin_w or None]
    if center.size <= 0:
        center = gray
    border_mask = np.ones_like(gray, dtype=bool)
    border_mask[margin_h:-margin_h or None, margin_w:-margin_w or None] = False
    border = gray[border_mask]
    center_mean = float(center.mean()) if center.size else float(gray.mean())
    border_mean = float(border.mean()) if border.size else float(gray.mean())
    contrast = abs(center_mean - border_mean) / 255.0
    return {"edge_density": edge_density, "center_contrast": contrast}


def normalize_graph_index(payload: Any, *, prompt_terms: list[str]) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    raw_nodes = data.get("objects")
    if raw_nodes is None:
        raw_nodes = data.get("nodes")
    raw_relations = data.get("relations")
    raw_signals = data.get("signals")

    nodes: list[dict[str, Any]] = []
    labels: list[str] = []
    if isinstance(raw_nodes, list):
        for item in raw_nodes:
            if not isinstance(item, dict):
                continue
            label = normalize_graph_label(item.get("label") or item.get("type") or item.get("name"))
            if not label:
                continue
            node = {
                "id": item.get("id") or label,
                "label": label,
                "bbox": item.get("bbox") if isinstance(item.get("bbox"), list) else [],
                "mask": item.get("mask") or item.get("mask_path") or "",
                "confidence": float(item.get("confidence") or item.get("score") or 0.0),
                "mask_area_ratio": float(item.get("mask_area_ratio") or 0.0),
                "bbox_area_ratio": float(item.get("bbox_area_ratio") or 0.0),
                "centroid_x": float(item.get("centroid_x") or 0.0),
                "centroid_y": float(item.get("centroid_y") or 0.0),
                "touches_border": bool(item.get("touches_border")),
            }
            nodes.append(node)
            labels.append(label)

    relations: list[dict[str, Any]] = []
    if isinstance(raw_relations, list):
        for item in raw_relations:
            if not isinstance(item, dict):
                continue
            subject = normalize_graph_label(item.get("from") or item.get("subject"))
            relation = normalize_graph_relation(item.get("relation") or item.get("type") or item.get("predicate"))
            target = normalize_graph_label(item.get("to") or item.get("object") or item.get("target"))
            if not subject or not relation or not target:
                continue
            relations.append(
                {
                    "from": subject,
                    "relation": relation,
                    "to": target,
                    "score": float(item.get("score") or item.get("confidence") or 0.0),
                }
            )

    signals: dict[str, bool] = {signal: False for signal in DEFAULT_GRAPH_INDEX_SIGNALS}
    if isinstance(raw_signals, dict):
        for key, value in raw_signals.items():
            if isinstance(value, bool):
                signals[str(key)] = value
            elif isinstance(value, (int, float)):
                signals[str(key)] = float(value) > 0.0
            elif value is not None:
                signals[str(key)] = bool(value)

    for label in labels:
        signals[f"has_{label.replace(' ', '_')}"] = True

    for relation in relations:
        relation_key = f"{relation['from'].replace(' ', '_')}__{relation['relation']}__{relation['to'].replace(' ', '_')}"
        signals[relation_key] = True

    return {
        "backend": str(data.get("backend") or "http").strip() or "http",
        "prompt_terms": list(prompt_terms),
        "objects": nodes,
        "relations": relations,
        "signals": signals,
        "raw": data,
    }


def normalize_graph_label(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text).strip()
    text = " ".join(text.split())
    return text


def normalize_graph_relation(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"[^0-9a-z_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def infer_frame_type(section: dict[str, Any], operation: dict[str, Any]) -> str:
    text = " ".join(
        str(item or "")
        for item in (
            operation.get("operation_text"),
            operation.get("business_frame_text"),
            section.get("title"),
            section.get("text"),
        )
    ).strip()
    for frame_type, keywords in FRAME_TYPE_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return frame_type
    return "overview_frame"


def section_query_operations(section: dict[str, Any]) -> list[dict[str, Any]]:
    query_graph = section.get("query_graph")
    if not isinstance(query_graph, dict):
        return []
    business_frame_text = str(section.get("business_frame_text") or "").strip()
    return [
        {
            "index": 1,
            "operation_text": str(section.get("title") or "").strip(),
            "source_segment_indices": section.get("source_chunks") or section.get("source_segment_indices") or [],
            "priority": "medium",
            "polished_text": str(section.get("polished_text") or "").strip(),
            "business_frame_text": business_frame_text,
            "query_graph": query_graph,
            "start_seconds": section.get("start_seconds"),
            "end_seconds": section.get("end_seconds"),
            "start_time": section.get("start_time"),
            "end_time": section.get("end_time"),
        }
    ]


def tokenize_query_terms(text: str) -> list[str]:
    normalized = re.sub(r"[\s,，。.!?；;:：、/\\()（）\[\]【】\"'“”‘’]+", " ", str(text or "")).strip()
    if not normalized:
        return []
    tokens: list[str] = []
    for raw in normalized.split():
        token = raw.strip()
        if not token:
            continue
        label = TERM_SYNONYMS.get(token) or TERM_SYNONYMS.get(token.lower())
        if label:
            tokens.append(label)
            continue
        if len(token) <= 2:
            continue
        tokens.append(token.lower())
    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        result.append(token)
        seen.add(token)
    return result


def normalize_visual_terms(value: Any) -> list[str]:
    raw_items = value if isinstance(value, list) else [value]
    terms: list[str] = []
    for item in raw_items:
        if isinstance(item, dict):
            item = item.get("label") or item.get("term") or item.get("name")
        term = normalize_graph_label(item)
        if not term:
            continue
        label = TERM_SYNONYMS.get(term) or TERM_SYNONYMS.get(term.lower()) or term
        terms.append(normalize_graph_label(label))
    return [term for term in dict.fromkeys(terms) if term]


def build_query_spec(*, section: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    operation_text = str(operation.get("operation_text") or "").strip()
    business_frame_text = str(operation.get("business_frame_text") or section.get("business_frame_text") or "").strip()
    polished_text = str(operation.get("polished_text") or section.get("polished_text") or "").strip()
    frame_type = infer_frame_type(section, operation)
    try:
        query_graph = normalize_query_graph(operation.get("query_graph") or section.get("query_graph"))
    except QueryGraphError as exc:
        raise RuntimeError(str(exc)) from exc
    visual_terms = query_graph_prompt_terms(query_graph)
    query_terms = tokenize_query_terms(
        f"{business_frame_text} {polished_text} {operation_text} {section.get('title') or ''}"
    )
    negative_labels = ["blur", "occluded"]
    return {
        "frame_type": frame_type,
        "target": business_frame_text or operation_text or str(section.get("title") or ""),
        "business_frame_text": business_frame_text,
        "polished_text": polished_text,
        "query_graph": query_graph,
        "visual_terms": visual_terms,
        "query_graph_terms": visual_terms,
        "query_terms": query_terms,
        "required_labels": visual_terms,
        "preferred_labels": [],
        "negative_labels": negative_labels,
        "operation_priority": str(operation.get("priority") or "medium"),
        "operation_text": operation_text,
    }


def query_spec_to_signals(query_spec: dict[str, Any]) -> list[str]:
    frame_type = str(query_spec.get("frame_type") or "overview_frame")
    labels = list(dict.fromkeys([str(item) for item in query_spec.get("query_graph_terms") or query_spec.get("required_labels") or [] if str(item).strip()]))
    if frame_type == "alignment_frame":
        labels.extend(["clear_key_region", "close_up"])
    elif frame_type == "operation_frame":
        labels.extend(["clear_key_region", "detail_rich"])
    elif frame_type == "wiring_frame":
        labels.extend(["clear_key_region", "detail_rich"])
    elif frame_type == "completion_frame":
        labels.extend(["clear_key_region", "sharp"])
    else:
        labels.extend(["close_up", "detail_rich"])
    return [label for label in dict.fromkeys(labels) if label]


def graph_index_match_score(*, query_spec: dict[str, Any], graph_index: dict[str, Any], quality_score_value: float) -> tuple[float, list[str], list[str], list[str]]:
    query_graph = query_spec.get("query_graph")
    if not isinstance(query_graph, dict):
        raise RuntimeError("query_graph is required for semantic scoring")

    graph_index = graph_index if isinstance(graph_index, dict) else {}
    signals = graph_index.get("signals")
    if not isinstance(signals, dict):
        signals = {}
    objects = graph_index.get("objects")
    if not isinstance(objects, list):
        objects = []

    image_size: dict[str, Any] = {}
    image = graph_index.get("image")
    if isinstance(image, dict) and isinstance(image.get("size"), dict):
        image_size = image["size"]
    elif isinstance(graph_index.get("raw"), dict):
        raw = graph_index["raw"]
        detections = raw.get("detections")
        if isinstance(detections, dict) and isinstance(detections.get("image_size"), dict):
            image_size = detections["image_size"]
    width = max(1, int(image_size.get("width") or 0))
    height = max(1, int(image_size.get("height") or 0))
    if width <= 1 or height <= 1:
        max_x = 0.0
        max_y = 0.0
        for item in objects:
            if not isinstance(item, dict):
                continue
            bbox = graph_object_bbox(item)
            if len(bbox) >= 4:
                max_x = max(max_x, float(bbox[2]))
                max_y = max(max_y, float(bbox[3]))
        width = max(width, int(math.ceil(max_x)) if max_x > 0 else 1)
        height = max(height, int(math.ceil(max_y)) if max_y > 0 else 1)

    relation_keys: set[str] = set()
    raw_relations = graph_index.get("relations")
    if isinstance(raw_relations, list):
        for item in raw_relations:
            if not isinstance(item, dict):
                continue
            subject = normalize_graph_label(item.get("from") or item.get("subject"))
            relation = normalize_graph_relation(item.get("relation") or item.get("type") or item.get("predicate"))
            target = normalize_graph_label(item.get("to") or item.get("object") or item.get("target"))
            if subject and relation and target:
                relation_keys.add(f"{subject}__{relation}__{target}")
                signals[f"{subject.replace(' ', '_')}__{relation}__{target.replace(' ', '_')}"] = True

    frame_type = str(query_spec.get("frame_type") or "overview_frame").strip() or "overview_frame"
    negative_labels = [normalize_graph_label(item) for item in query_spec.get("negative_labels") or [] if str(item).strip()]

    query_nodes = [item for item in query_graph.get("nodes") or [] if isinstance(item, dict)]
    query_edges = [item for item in query_graph.get("edges") or [] if isinstance(item, dict)]
    object_query_nodes = query_nodes
    query_node_by_id = {normalize_graph_id(node.get("id")): node for node in query_nodes if normalize_graph_id(node.get("id"))}

    matched: list[str] = []
    missing: list[str] = []
    negative_hits: list[str] = []
    matched_objects: list[dict[str, Any]] = []
    matched_signatures: set[tuple[str, tuple[float, float, float, float]]] = set()
    confidence_by_label: dict[str, float] = {}
    objects_by_label: dict[str, list[dict[str, Any]]] = {}
    hand_objects: list[dict[str, Any]] = []

    for item in objects:
        if not isinstance(item, dict):
            continue
        label = normalize_graph_label(item.get("label"))
        if not label:
            continue
        confidence_by_label[label] = max(confidence_by_label.get(label, 0.0), float(item.get("confidence") or 0.0))
        objects_by_label.setdefault(label, []).append(item)
        if label == "hand":
            hand_objects.append(item)

    def signal_present(name: str) -> bool:
        if not name:
            return False
        normalized = normalize_graph_label(name)
        if normalized in confidence_by_label:
            return True
        if signals.get(normalized) is True:
            return True
        if normalized in relation_keys:
            return True
        prefixed = f"has_{normalized.replace(' ', '_')}"
        if signals.get(prefixed) is True:
            return True
        return False

    def object_signature(item: dict[str, Any]) -> tuple[str, tuple[float, float, float, float]]:
        bbox = graph_object_bbox(item)
        return (
            normalize_graph_label(item.get("label")),
            tuple(round(float(value), 2) for value in bbox[:4]),
        )

    def labels_compatible(query_label: str, candidate_label: str) -> bool:
        if not query_label or not candidate_label:
            return False
        if query_label == candidate_label:
            return True
        if query_label in candidate_label or candidate_label in query_label:
            return True
        query_tokens = set(query_label.split())
        candidate_tokens = set(candidate_label.split())
        if not query_tokens or not candidate_tokens:
            return False
        overlap = query_tokens & candidate_tokens
        return bool(overlap) and (
            len(query_tokens) <= 2
            or len(candidate_tokens) <= 2
            or len(overlap) / max(1, min(len(query_tokens), len(candidate_tokens))) >= 0.5
        )

    def choose_best_object(label: str) -> dict[str, Any] | None:
        candidates: list[dict[str, Any]] = []
        for candidate_label, items in objects_by_label.items():
            if labels_compatible(label, candidate_label):
                candidates.extend(items)
        if not candidates:
            return None

        def candidate_score(item: dict[str, Any]) -> tuple[float, float, float, float]:
            candidate_label = normalize_graph_label(item.get("label"))
            label_bonus = 1.0 if candidate_label == label else 0.85 if labels_compatible(label, candidate_label) else 0.0
            return (
                label_bonus,
                graph_object_quality_score(item, width=width, height=height, frame_type=frame_type),
                clamp_unit(float(item.get("confidence") or 0.0)),
                0.0 if bool(item.get("touches_border")) else 1.0,
            )

        return max(candidates, key=candidate_score)

    def node_match_quality(node: dict[str, Any], item: dict[str, Any]) -> float:
        label = normalize_graph_label(node.get("label"))
        candidate_label = normalize_graph_label(item.get("label"))
        label_bonus = 1.0 if candidate_label == label else 0.85 if labels_compatible(label, candidate_label) else 0.0
        confidence = clamp_unit(float(item.get("confidence") or 0.0))
        object_quality = graph_object_quality_score(item, width=width, height=height, frame_type=frame_type)
        return clamp_unit(0.50 * object_quality + 0.30 * confidence + 0.20 * label_bonus)

    def query_relation_matches_graph(edge_relation: str) -> bool:
        normalized = normalize_graph_relation(edge_relation)
        if not normalized:
            return False
        if normalized in {"near", "overlaps", "occludes"}:
            return True
        if normalized in {"fixed_to", "attached_to", "connected_to", "mounted_to", "inserted_into", "has_part", "part_of"}:
            return True
        return False

    def edge_match_quality(edge: dict[str, Any], left: dict[str, Any], right: dict[str, Any]) -> float:
        relation = normalize_graph_relation(edge.get("relation"))
        pair_score = graph_pair_spatial_score(left, right, width=width, height=height, relation_keys=relation_keys)
        relation_key = f"{normalize_graph_label(left.get('label'))}__{relation}__{normalize_graph_label(right.get('label'))}"
        reverse_key = f"{normalize_graph_label(right.get('label'))}__{relation}__{normalize_graph_label(left.get('label'))}"
        explicit_relation = relation_key in relation_keys or reverse_key in relation_keys
        relation_bonus = 1.0 if explicit_relation else 0.0
        if not explicit_relation and query_relation_matches_graph(relation):
            relation_bonus = max(relation_bonus, pair_score)
        return clamp_unit(0.70 * pair_score + 0.30 * relation_bonus)

    def query_item_weight(item: dict[str, Any]) -> float:
        return max(0.0, float(item.get("weight") or 0.0)) or 1.0

    matched_node_scores: list[float] = []
    total_node_weight = 0.0
    required_missing_count = 0
    for node in object_query_nodes:
        label = normalize_graph_label(node.get("label"))
        if not label:
            continue
        weight = query_item_weight(node)
        total_node_weight += weight
        matched_object = choose_best_object(label)
        if matched_object is None:
            if bool(node.get("required")):
                missing.append(label)
                required_missing_count += 1
            matched_node_scores.append(0.0)
            continue
        signature = object_signature(matched_object)
        if signature not in matched_signatures:
            matched_signatures.add(signature)
            matched_objects.append(matched_object)
        matched.append(label)
        matched_node_scores.append(node_match_quality(node, matched_object))
        confidence_by_label[label] = max(confidence_by_label.get(label, 0.0), float(matched_object.get("confidence") or 0.0))

    node_score = sum(
        query_item_weight(node) * matched_node_scores[index]
        for index, node in enumerate(object_query_nodes)
    ) / max(1e-6, total_node_weight)

    matched_edge_scores: list[float] = []
    total_edge_weight = 0.0
    required_missing_edge_count = 0
    for edge in query_edges:
        source_id = normalize_graph_id(edge.get("from") or edge.get("source"))
        target_id = normalize_graph_id(edge.get("to") or edge.get("target"))
        relation = normalize_graph_relation(edge.get("relation") or edge.get("type"))
        source_node = query_node_by_id.get(source_id)
        target_node = query_node_by_id.get(target_id)
        weight = query_item_weight(edge)
        total_edge_weight += weight
        left_node = source_node if source_node in object_query_nodes else None
        right_node = target_node if target_node in object_query_nodes else None
        left_object = choose_best_object(normalize_graph_label(left_node.get("label"))) if left_node else None
        right_object = choose_best_object(normalize_graph_label(right_node.get("label"))) if right_node else None
        if left_object is None or right_object is None:
            if bool(edge.get("required")):
                missing.append(f"{source_id}__{relation}__{target_id}")
                required_missing_edge_count += 1
            matched_edge_scores.append(0.0)
            continue
        edge_score = edge_match_quality(edge, left_object, right_object)
        matched_edge_scores.append(edge_score)
        if edge_score > 0.0:
            matched.append(f"{source_id}__{relation}__{target_id}")

    edge_score = sum(
        query_item_weight(edge) * matched_edge_scores[index]
        for index, edge in enumerate(query_edges)
    ) / max(1e-6, total_edge_weight)

    if matched_objects:
        object_quality_scores = [
            graph_object_quality_score(item, width=width, height=height, frame_type=frame_type)
            for item in matched_objects
        ]
        object_quality = sum(object_quality_scores) / len(object_quality_scores)
    else:
        object_quality = 0.0

    pair_scores: list[float] = []
    for index, left in enumerate(matched_objects):
        left_label = normalize_graph_label(left.get("label"))
        if not left_label:
            continue
        for right in matched_objects[index + 1 :]:
            right_label = normalize_graph_label(right.get("label"))
            if not right_label:
                continue
            pair_scores.append(
                graph_pair_spatial_score(
                    left,
                    right,
                    width=width,
                    height=height,
                    relation_keys=relation_keys,
                )
            )
    if pair_scores:
        spatial_score = sum(pair_scores) / len(pair_scores)
    elif matched_objects:
        spatial_score = object_quality
    else:
        spatial_score = 0.0

    hand_object = None
    if hand_objects:
        hand_object = max(
            hand_objects,
            key=lambda item: (
                graph_object_area_ratio(item),
                clamp_unit(float(item.get("confidence") or 0.0)),
                0.0 if bool(item.get("touches_border")) else 1.0,
            ),
        )
    visibility_score = graph_visibility_score(
        matched_objects=matched_objects,
        hand_object=hand_object,
        width=width,
        height=height,
        signals=signals,
    )

    expected_signals = [
        signal
        for signal in query_spec_to_signals(query_spec)
        if signal not in (query_graph_prompt_terms(query_graph) if query_graph else [])
    ]
    signal_score = 0.0
    if expected_signals:
        signal_hits = sum(1 for signal in expected_signals if signal_present(signal))
        signal_score = signal_hits / len(expected_signals)

    score = 0.48 * clamp_unit(node_score)
    score += 0.32 * clamp_unit(edge_score)
    score += 0.08 * clamp_unit(visibility_score if matched_objects else 0.0)
    score += 0.05 * clamp_unit(float(quality_score_value))
    score += 0.04 * clamp_unit(signal_score)
    score += 0.03 * clamp_unit(spatial_score)

    if bool(signals.get("blur")) or bool(signals.get("not_blurry") is False):
        score -= 0.10
        negative_hits.append("blur")
    if bool(signals.get("occluded")):
        score -= 0.08 + 0.10 * (1.0 - clamp_unit(visibility_score))
        negative_hits.append("occluded")
    if any(bool(item.get("touches_border")) for item in matched_objects):
        score -= 0.04
        negative_hits.append("border_cut")

    for label in negative_labels:
        if signal_present(label):
            score -= 0.06
            negative_hits.append(label)

    if required_missing_count > 0:
        score -= min(0.18, 0.06 * required_missing_count)
    if required_missing_edge_count > 0:
        score -= min(0.18, 0.05 * required_missing_edge_count)

    matched = list(dict.fromkeys(matched))
    missing = list(dict.fromkeys(missing))
    negative_hits = list(dict.fromkeys(negative_hits))

    return clamp_unit(score), matched, missing, negative_hits


def compute_section_frame_similarities(
    *,
    sections: list[dict[str, Any]],
    candidates_by_operation: dict[tuple[int, int], list[FrameFeature]],
    progress_callback: Callable[[str, int, int, str], None] | None = None,
) -> dict[tuple[int, int, int], float]:
    details = compute_section_frame_match_details(
        sections=sections,
        candidates_by_operation=candidates_by_operation,
        progress_callback=progress_callback,
    )
    return {key: value.score for key, value in details.items()}


def compute_section_frame_match_details(
    *,
    sections: list[dict[str, Any]],
    candidates_by_operation: dict[tuple[int, int], list[FrameFeature]],
    progress_callback: Callable[[str, int, int, str], None] | None = None,
) -> dict[tuple[int, int, int], FrameMatchDetail]:
    return compute_section_frame_match_details_finetuned(
        sections=sections,
        candidates_by_operation=candidates_by_operation,
        progress_callback=progress_callback,
    )


def compute_section_frame_match_details_finetuned(
    *,
    sections: list[dict[str, Any]],
    candidates_by_operation: dict[tuple[int, int], list[FrameFeature]],
    progress_callback: Callable[[str, int, int, str], None] | None = None,
) -> dict[tuple[int, int, int], FrameMatchDetail]:
    from app.services.finetuned_frame_matching import segment_images_finetuned_detect

    query_specs: dict[tuple[int, int], dict[str, Any]] = {}
    for section in sections:
        section_index = int(section["index"])
        for operation in section_query_operations(section):
            query_specs[(section_index, 1)] = build_query_spec(section=section, operation=operation)

    all_candidates = unique_candidates(candidates_by_operation)
    total_candidates = len(all_candidates)
    notify_progress(progress_callback, "semantic_matching", 0, total_candidates, "Business frame filter: finetuned detect, SAM mask, graph matching")
    image_files = [
        (item.frame.object_key.rsplit("/", 1)[-1], item.path.read_bytes())
        for item in all_candidates
    ]
    segmentation_results = segment_images_finetuned_detect(image_files=image_files)
    segmented_by_filename = {result.filename: result for result in segmentation_results}

    details: dict[tuple[int, int, int], FrameMatchDetail] = {}
    processed_candidates = 0
    for section in sections:
        section_index = int(section["index"])
        for operation in section_query_operations(section):
            operation_index = 1
            query_spec = query_specs.get((section_index, operation_index)) or build_query_spec(section=section, operation=operation)
            for item in candidates_by_operation.get((section_index, operation_index), []):
                processed_candidates += 1
                notify_progress(
                    progress_callback,
                    "semantic_matching",
                    min(processed_candidates, total_candidates),
                    total_candidates,
                    "Business frame filter: graph matching",
                )
                filename = item.frame.object_key.rsplit("/", 1)[-1]
                result = segmented_by_filename.get(filename)
                if result is None:
                    details[(section_index, operation_index, int(item.frame.id))] = FrameMatchDetail(
                        score=0.0,
                        matched=[],
                        missing=list(query_spec.get("required_labels") or []),
                        negative_hits=[],
                        objects=[],
                        relations=[],
                        signals={},
                        quality=item.quality,
                        backend="finetuned_yolo_world_sam",
                    )
                    continue
                image_size = image_size_from_quality(result.quality)
                relations = infer_graph_relations(result.objects, image_size=image_size)
                signals = infer_graph_signals(
                    quality=result.quality,
                    image_size=image_size,
                    objects=result.objects,
                    relations=relations,
                )
                graph_index = {
                    "backend": "finetuned_yolo_world_sam",
                    "prompt_terms": query_spec.get("query_graph_terms") or [],
                    "objects": result.objects,
                    "relations": relations,
                    "signals": signals,
                    "quality": result.quality,
                    "image": {"size": image_size},
                    "raw": {},
                }
                item.graph_index = graph_index
                score, matched, missing, negative_hits = graph_index_match_score(
                    query_spec=query_spec,
                    graph_index=graph_index,
                    quality_score_value=quality_score(result.quality),
                )
                details[(section_index, operation_index, int(item.frame.id))] = FrameMatchDetail(
                    score=score,
                    matched=matched,
                    missing=missing,
                    negative_hits=negative_hits,
                    objects=result.objects,
                    relations=relations,
                    signals=signals,
                    quality=result.quality,
                    bbox_image_base64=result.bbox_image_base64,
                    mask_image_base64=result.mask_image_base64,
                    mask_available=result.mask_available,
                    backend="finetuned_yolo_world_sam",
                    devices={
                        "mobile_sam": result.mobile_sam_device,
                        "yolo_world": result.yolo_world_device,
                    },
                )

    return details


def notify_progress(
    callback: Callable[[str, int, int, str], None] | None,
    stage: str,
    processed: int,
    total: int,
    message: str,
) -> None:
    if callback is None:
        return
    callback(stage, processed, total, message)


def best_section_frame_similarity(
    *,
    section: dict[str, Any],
    frame_id: int,
    similarities: dict[tuple[int, int, int], float],
) -> float:
    section_index = int(section["index"])
    scores = [
        similarities.get((section_index, 1, frame_id), 0.0)
    ]
    return max(scores) if scores else 0.0


def build_report(
    *,
    job: VideoJob,
    sections: list[dict[str, Any]],
    frames: list[VideoFrame],
    candidates_by_section: dict[int, list[FrameFeature]],
    candidates_by_operation: dict[tuple[int, int], list[FrameFeature]],
    raw_statuses_by_section: dict[int, dict[int, dict[str, Any]]],
    similarities: dict[tuple[int, int, int], float],
    match_details: dict[tuple[int, int, int], FrameMatchDetail] | None = None,
) -> dict[str, Any]:
    frames_by_id = {int(frame.id): frame for frame in frames}
    candidate_ids = {int(item.frame.id) for item in unique_candidates(candidates_by_operation)}
    operation_candidate_total = sum(len(candidates) for candidates in candidates_by_operation.values())
    query_graph_count = sum(1 for section in sections if isinstance(section.get("query_graph"), dict))
    payload_sections = []

    for section in sections:
        section_index = int(section["index"])
        raw_statuses = raw_statuses_by_section.get(section_index, {})
        raw_frames = [
            frame_ref(job.id, frame, extra=raw_statuses.get(int(frame.id)))
            for frame in frames_for_section(frames, section, padding_seconds=SECTION_FRAME_PADDING_SECONDS)
        ]
        section_operation_candidates = unique_candidates(
            {
                operation_key: operation_candidates
                for operation_key, operation_candidates in candidates_by_operation.items()
                if operation_key[0] == section_index
            }
        )
        ranked = sorted(
            [item.frame for item in section_operation_candidates],
            key=lambda frame: best_section_frame_similarity(
                section=section,
                frame_id=int(frame.id),
                similarities=similarities,
            ),
            reverse=True,
        )
        semantic_frames = [
            {
                **frame_ref(job.id, frame),
                "score": best_section_frame_similarity(
                    section=section,
                    frame_id=int(frame.id),
                    similarities=similarities,
                ),
                "rank": rank,
                "is_candidate": int(frame.id) in candidate_ids,
            }
            for rank, frame in enumerate(ranked, start=1)
        ]
        query_graph_matches = build_query_graph_matches(
            job=job,
            section=section,
            candidates_by_operation=candidates_by_operation,
            similarities=similarities,
            match_details=match_details or {},
        )
        window = section_time_window(section, padding_seconds=SECTION_FRAME_PADDING_SECONDS)
        payload_sections.append(
            {
                **section,
                "frame_window_start_seconds": window[0] if window else None,
                "frame_window_end_seconds": window[1] if window else None,
                "frame_window_padding_seconds": SECTION_FRAME_PADDING_SECONDS,
                "raw_frames": raw_frames,
                "candidate_business_frames": [
                    frame_ref(job.id, item.frame)
                    for item in section_operation_candidates
                ],
                "query_graph_matches": query_graph_matches,
                "semantic_frames": semantic_frames,
            }
        )
    return {
        "version": "1.1",
        "video_id": job.id,
        "graph_index_backend": "finetuned_yolo_world_sam",
        "section_frame_padding_seconds": SECTION_FRAME_PADDING_SECONDS,
        "summary": {
            "raw_frame_count": len(frames),
            "candidate_frame_count": len(candidate_ids),
            "section_candidate_frame_count": operation_candidate_total,
            "section_count": len(sections),
            "query_graph_count": query_graph_count,
        },
        "sections": payload_sections,
        "candidate_frames": [frame_ref(job.id, frames_by_id[frame_id]) for frame_id in sorted(candidate_ids)],
    }


def build_query_graph_matches(
    *,
    job: VideoJob,
    section: dict[str, Any],
    candidates_by_operation: dict[tuple[int, int], list[FrameFeature]],
    similarities: dict[tuple[int, int, int], float],
    match_details: dict[tuple[int, int, int], FrameMatchDetail] | None = None,
) -> list[dict[str, Any]]:
    section_index = int(section["index"])
    matches: list[dict[str, Any]] = []
    for operation in section_query_operations(section):
        query_index = 1
        business_frame_text = str(operation.get("business_frame_text") or section.get("business_frame_text") or "")
        frames = [item.frame for item in candidates_by_operation.get((section_index, query_index), [])]
        ranked = sorted(
            frames,
            key=lambda frame: similarities.get((section_index, query_index, int(frame.id)), 0.0),
            reverse=True,
        )
        matches.append(
            {
                "query_index": query_index,
                "query_graph_index": query_index,
                "section_title": str(section.get("title") or ""),
                "business_frame_text": business_frame_text,
                "query_graph": section.get("query_graph") or {},
                "section_start_seconds": section.get("start_seconds"),
                "section_end_seconds": section.get("end_seconds"),
                "section_start_time": section.get("start_time"),
                "section_end_time": section.get("end_time"),
                "source": "section_query_graph",
                "frames": [
                    {
                        **frame_ref(job.id, frame),
                        "score": similarities.get((section_index, query_index, int(frame.id)), 0.0),
                        "rank": rank,
                        "matched_query_index": query_index,
                        "matched_query_graph_index": query_index,
                        "matched_section_title": str(section.get("title") or ""),
                        "matched_business_frame_text": business_frame_text,
                        "matched_query_source": "section_query_graph",
                        **frame_match_detail_payload(
                            (match_details or {}).get((section_index, query_index, int(frame.id)))
                        ),
                    }
                    for rank, frame in enumerate(ranked, start=1)
                ],
            }
        )
    return matches


def frame_match_detail_payload(detail: FrameMatchDetail | None) -> dict[str, Any]:
    if detail is None:
        return {}
    return {
        "matched": detail.matched,
        "missing": detail.missing,
        "negative_hits": detail.negative_hits,
        "objects": detail.objects,
        "relations": detail.relations,
        "signals": detail.signals,
        "quality": detail.quality,
        "bbox_image": detail.bbox_image_base64,
        "mask_image": detail.mask_image_base64,
        "mask_available": detail.mask_available,
        "match_backend": detail.backend,
        "devices": detail.devices or {},
    }


def frames_for_section(
    frames: list[VideoFrame],
    section: dict[str, Any],
    *,
    padding_seconds: float = 0.0,
) -> list[VideoFrame]:
    window = section_time_window(section, padding_seconds=padding_seconds)
    if window is None:
        return frames
    start, end = window
    return [
        frame
        for frame in frames
        if start <= float(frame.timestamp_seconds or frame.timestamp_ms / 1000 or 0.0) <= end
    ]


def section_time_window(section: dict[str, Any], *, padding_seconds: float = 0.0) -> tuple[float, float] | None:
    start = parse_float(section.get("start_seconds"))
    end = parse_float(section.get("end_seconds"))
    if start is None or end is None or end <= start:
        return None
    padding = max(0.0, float(padding_seconds or 0.0))
    return max(0.0, start - padding), end + padding


def frame_ref(video_id: str, frame: VideoFrame, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    filename = frame.object_key.rsplit("/", 1)[-1]
    payload = {
        "id": frame.id,
        "frame_id": frame.id,
        "timestamp_ms": frame.timestamp_ms,
        "timestamp_seconds": frame.timestamp_seconds,
        "timestamp_time": format_timestamp(float(frame.timestamp_seconds or frame.timestamp_ms / 1000 or 0.0)),
        "object_key": frame.object_key,
        "url": f"/api/videos/{video_id}/frames/{filename}",
        "selection_status": frame.selection_status or "pending",
        "selection_score": frame.selection_score,
        "matched_section_index": frame.matched_section_index,
    }
    if extra:
        payload.update(extra)
    return payload


def format_timestamp(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def parse_optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_source_indices(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    indices = []
    for item in value:
        try:
            indices.append(int(item))
        except (TypeError, ValueError):
            continue
    return sorted(dict.fromkeys(indices))


def parse_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
