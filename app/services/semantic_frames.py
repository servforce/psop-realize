from __future__ import annotations

import base64
import io
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from app.core.config import settings
from app.models.entities import VideoFrame, VideoJob
from app.services.frame_selection import analyze_image_quality, mark_frame_rejected, mark_frame_selected


SECTION_FRAME_PADDING_SECONDS = 2.0


@dataclass(slots=True)
class FrameFeature:
    frame: VideoFrame
    path: Path
    quality: dict[str, Any]
    quality_score: float
    hsv_histogram: list[float] | None = None
    phash: int | None = None


@dataclass(slots=True)
class DedupGroup:
    id: str
    items: list[FrameFeature]
    representative: FrameFeature


@dataclass(slots=True)
class SectionFilterResult:
    candidates_by_section: dict[int, list[FrameFeature]]
    candidates_by_operation: dict[tuple[int, int], list[FrameFeature]]
    raw_statuses_by_section: dict[int, dict[int, dict[str, Any]]]
    quality_by_frame_id: dict[int, dict[str, Any]]


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
    if not settings.video_semantic_embedding_api_key:
        raise RuntimeError("缺少 VIDEO_SEMANTIC_EMBEDDING_API_KEY，无法调用 qwen3-vl-embedding 进行语义帧匹配。")

    similarities = compute_section_frame_similarities(
        sections=sections,
        candidates_by_operation=filter_result.candidates_by_operation,
        progress_callback=progress_callback,
    )
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
            reason="Semantic frame candidate scored by qwen3-vl-embedding cosine similarity.",
            details={
                "stage": "semantic_matching",
                "embedding_model": settings.video_semantic_embedding_model,
                "best_section_index": section_index,
                "best_section_title": section.get("title") or "",
                "best_similarity": score,
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
        for operation in section.get("visual_operations") or []:
            if not isinstance(operation, dict):
                continue
            operation_index = parse_optional_int(operation.get("index"))
            if operation_index is None:
                continue
            operation_frames = frames_for_section(frames, operation, padding_seconds=SECTION_FRAME_PADDING_SECONDS)
            operation_frames_by_key[(section_index, operation_index)] = operation_frames
            relevant_frame_ids.update(int(frame.id) for frame in operation_frames)

    frames_to_evaluate = [frame for frame in frames if int(frame.id) in relevant_frame_ids]
    feature_by_frame_id: dict[int, FrameFeature] = {}
    quality_by_frame_id: dict[int, dict[str, Any]] = {}
    total_frames = len(frames_to_evaluate)
    notify_progress(progress_callback, "filtering_frames", 0, total_frames, "关键操作时间窗内图像质量过滤")
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
            notify_progress(progress_callback, "filtering_frames", index, total_frames, "关键操作时间窗内图像质量过滤")
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
            notify_progress(progress_callback, "filtering_frames", index, total_frames, "关键操作时间窗内图像质量过滤")
            continue

        feature_by_frame_id[frame_id] = FrameFeature(
            frame=frame,
            path=path,
            quality=quality.details,
            quality_score=quality_score(quality.details),
        )
        notify_progress(progress_callback, "filtering_frames", index, total_frames, "关键操作时间窗内图像质量过滤")

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

        for operation in section.get("visual_operations") or []:
            if not isinstance(operation, dict):
                continue
            operation_index = parse_optional_int(operation.get("index"))
            if operation_index is None:
                continue
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
                dedup_group_id = f"section_{section_index}_operation_{operation_index}_{group.id}"
                dedup_report["groups"].append(
                    {
                        "section_index": section_index,
                        "operation_index": operation_index,
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
                            "operation_index": operation_index,
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
                        "filter_reason": "Rejected as a duplicate frame in this visual operation window.",
                        "dedup_group_id": f"section_{section_index}_operation_{operation_index}_{group.id}",
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
                reason="Frame is outside all visual operation windows.",
                details={
                    "stage": "visual_operation_window",
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
        visual_operations = extract_visual_operations(section, section_index=section_index, title=title, text=text)
        frame_queries = [
            str(operation.get("frame_query") or "")
            for operation in visual_operations
            if str(operation.get("frame_query") or "").strip()
        ]
        embedding_text = "\n".join(frame_queries).strip()
        sections.append(
            {
                "index": section_index,
                "title": title,
                "text": text,
                "frame_queries": frame_queries,
                "frame_queries_source": "visual_operations" if visual_operations else "none",
                "visual_operations": visual_operations,
                "embedding_text": embedding_text,
                "start_seconds": parse_float(section.get("start_seconds")),
                "end_seconds": parse_float(section.get("end_seconds")),
                "start_time": section.get("start_time"),
                "end_time": section.get("end_time"),
            }
        )
    return sections


def extract_visual_operations(section: dict[str, Any], *, section_index: int, title: str, text: str) -> list[dict[str, Any]]:
    raw_operations = section.get("visual_operations")
    if isinstance(raw_operations, list):
        operations = []
        for index, raw_operation in enumerate(raw_operations, start=1):
            if not isinstance(raw_operation, dict):
                continue
            operation_index = parse_optional_int(raw_operation.get("index")) or index
            frame_query = " ".join(str(raw_operation.get("frame_query") or "").split()).strip()
            if not frame_query:
                continue
            operation_text = " ".join(
                str(raw_operation.get("operation_text") or raw_operation.get("text") or frame_query).split()
            ).strip()
            operations.append(
                {
                    "index": operation_index,
                    "operation_text": operation_text,
                    "source_segment_indices": normalize_source_indices(
                        raw_operation.get("source_segment_indices") or raw_operation.get("source_chunks")
                    ),
                    "priority": normalize_priority(raw_operation.get("priority")),
                    "frame_query": frame_query,
                    "start_seconds": parse_float(raw_operation.get("start_seconds")),
                    "end_seconds": parse_float(raw_operation.get("end_seconds")),
                    "start_time": raw_operation.get("start_time"),
                    "end_time": raw_operation.get("end_time"),
                }
            )
        return operations

    return []


def normalize_priority(value: Any) -> str:
    priority = str(value or "medium").strip().lower()
    return priority if priority in {"high", "medium", "low"} else "medium"


def normalize_frame_queries(value: Any, *, title: str, text: str) -> list[str]:
    raw_items = value if isinstance(value, list) else [value]
    queries: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        query = " ".join(str(item or "").split()).strip()
        if not query or query in seen:
            continue
        queries.append(query)
        seen.add(query)
        if len(queries) >= 3:
            break

    if queries:
        return queries

    fallback = f"{title}\n{text}".strip()
    return [fallback] if fallback else []


def normalize_frame_queries_source(
    value: Any,
    *,
    raw_frame_queries: Any,
    frame_queries: list[str],
    title: str,
    text: str,
) -> str:
    source = str(value or "").strip()
    if source in {"model", "fallback_title_text"}:
        return source

    raw_items = raw_frame_queries if isinstance(raw_frame_queries, list) else [raw_frame_queries]
    has_raw_query = any(str(item or "").strip() for item in raw_items)
    fallback = f"{title}\n{text}".strip()
    if not has_raw_query:
        return "fallback_title_text"
    if fallback and len(frame_queries) == 1 and frame_queries[0] == fallback:
        return "fallback_title_text"
    return "model"


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


def compute_section_frame_similarities(
    *,
    sections: list[dict[str, Any]],
    candidates_by_operation: dict[tuple[int, int], list[FrameFeature]],
    progress_callback: Callable[[str, int, int, str], None] | None = None,
) -> dict[tuple[int, int, int], float]:
    client = QwenVlEmbeddingClient.from_settings()
    section_vectors: dict[tuple[int, int], list[float]] = {}
    for section in sections:
        section_index = int(section["index"])
        for operation in section.get("visual_operations") or []:
            if not isinstance(operation, dict):
                continue
            operation_index = parse_optional_int(operation.get("index"))
            if operation_index is None:
                continue
            section_vectors[(section_index, operation_index)] = normalize_vector(
                client.embed_text(str(operation.get("frame_query") or ""))
            )

    unique_frame_candidates = unique_candidates(candidates_by_operation)
    frame_vectors = {}
    total_candidates = len(unique_frame_candidates)
    for index, item in enumerate(unique_frame_candidates, start=1):
        notify_progress(progress_callback, "semantic_matching", index, total_candidates, "qwen3-vl-embedding 图片匹配")
        frame_vectors[int(item.frame.id)] = normalize_vector(client.embed_image(item.path))

    similarities: dict[tuple[int, int, int], float] = {}
    for section in sections:
        section_index = int(section["index"])
        for operation in section.get("visual_operations") or []:
            if not isinstance(operation, dict):
                continue
            operation_index = parse_optional_int(operation.get("index"))
            if operation_index is None:
                continue
            text_vector = section_vectors.get((section_index, operation_index), [])
            for item in candidates_by_operation.get((section_index, operation_index), []):
                frame_id = int(item.frame.id)
                similarities[(section_index, operation_index, frame_id)] = cosine(text_vector, frame_vectors[frame_id])
    return similarities


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


class QwenVlEmbeddingClient:
    def __init__(self, *, api_key: str, base_url: str, model: str, timeout_seconds: float) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls) -> "QwenVlEmbeddingClient":
        return cls(
            api_key=settings.video_semantic_embedding_api_key,
            base_url=settings.video_semantic_embedding_base_url,
            model=settings.video_semantic_embedding_model,
            timeout_seconds=settings.video_semantic_embedding_timeout_seconds,
        )

    def embed_text(self, text: str) -> list[float]:
        return self._embed_content({"text": text})

    def embed_image(self, path: Path) -> list[float]:
        data_url = image_data_url(path.read_bytes())
        return self._embed_content({"image": data_url})

    def _embed_content(self, content: dict[str, Any]) -> list[float]:
        payload = {"model": self.model, "input": {"contents": [content]}}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = self._embedding_url()
        with httpx.Client(timeout=self.timeout_seconds, trust_env=False) as client:
            response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        embedding = find_embedding(response.json())
        if not embedding:
            raise RuntimeError("qwen3-vl-embedding response did not include an embedding vector")
        return embedding

    def _embedding_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/api/v1"):
            base = base[: -len("/api/v1")]
        return f"{base}/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"


def image_data_url(image_bytes: bytes) -> str:
    return f"data:image/jpeg;base64,{base64.b64encode(image_bytes).decode('ascii')}"


def find_embedding(value: Any) -> list[float]:
    if isinstance(value, dict):
        for key in ("embedding", "multimodal_embedding", "vector"):
            item = value.get(key)
            if is_number_list(item):
                return [float(number) for number in item]
        for item in value.values():
            found = find_embedding(item)
            if found:
                return found
    if isinstance(value, list):
        if is_number_list(value):
            return [float(number) for number in value]
        for item in value:
            found = find_embedding(item)
            if found:
                return found
    return []


def is_number_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, (int, float)) for item in value)


def normalize_vector(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return vector
    return [value / norm for value in vector]


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return max(-1.0, min(1.0, sum(a * b for a, b in zip(left, right))))


def best_section_frame_similarity(
    *,
    section: dict[str, Any],
    frame_id: int,
    similarities: dict[tuple[int, int, int], float],
) -> float:
    section_index = int(section["index"])
    operations = section.get("visual_operations") if isinstance(section.get("visual_operations"), list) else []
    scores = [
        similarities.get((section_index, int(operation.get("index") or 0), frame_id), 0.0)
        for operation in operations
        if isinstance(operation, dict)
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
) -> dict[str, Any]:
    frames_by_id = {int(frame.id): frame for frame in frames}
    candidate_ids = {int(item.frame.id) for item in unique_candidates(candidates_by_operation)}
    operation_candidate_total = sum(len(candidates) for candidates in candidates_by_operation.values())
    visual_operation_count = sum(
        len(section.get("visual_operations") or [])
        for section in sections
        if isinstance(section.get("visual_operations"), list)
    )
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
                "similarity": best_section_frame_similarity(
                    section=section,
                    frame_id=int(frame.id),
                    similarities=similarities,
                ),
                "rank": rank,
                "is_candidate": int(frame.id) in candidate_ids,
            }
            for rank, frame in enumerate(ranked, start=1)
        ]
        frame_query_matches = build_frame_query_matches(
            job=job,
            section=section,
            candidates_by_operation=candidates_by_operation,
            similarities=similarities,
        )
        window = section_time_window(section, padding_seconds=SECTION_FRAME_PADDING_SECONDS)
        payload_sections.append(
            {
                **section,
                "frame_window_start_seconds": window[0] if window else None,
                "frame_window_end_seconds": window[1] if window else None,
                "frame_window_padding_seconds": SECTION_FRAME_PADDING_SECONDS,
                "raw_frames": raw_frames,
                "frame_query_matches": frame_query_matches,
                "semantic_frames": semantic_frames,
            }
        )
    return {
        "version": "1.1",
        "video_id": job.id,
        "embedding_model": settings.video_semantic_embedding_model,
        "embedding_enabled": bool(settings.video_semantic_embedding_api_key),
        "section_frame_padding_seconds": SECTION_FRAME_PADDING_SECONDS,
        "summary": {
            "raw_frame_count": len(frames),
            "candidate_frame_count": len(candidate_ids),
            "section_candidate_frame_count": operation_candidate_total,
            "section_count": len(sections),
            "frame_query_count": visual_operation_count,
            "visual_operation_count": visual_operation_count,
        },
        "sections": payload_sections,
        "candidate_frames": [frame_ref(job.id, frames_by_id[frame_id]) for frame_id in sorted(candidate_ids)],
    }


def build_frame_query_matches(
    *,
    job: VideoJob,
    section: dict[str, Any],
    candidates_by_operation: dict[tuple[int, int], list[FrameFeature]],
    similarities: dict[tuple[int, int, int], float],
) -> list[dict[str, Any]]:
    section_index = int(section["index"])
    visual_operations = section.get("visual_operations") if isinstance(section.get("visual_operations"), list) else []
    matches: list[dict[str, Any]] = []
    for operation in visual_operations:
        if not isinstance(operation, dict):
            continue
        query_index = parse_optional_int(operation.get("index"))
        if query_index is None:
            continue
        query = str(operation.get("frame_query") or "")
        frames = [item.frame for item in candidates_by_operation.get((section_index, query_index), [])]
        ranked = sorted(
            frames,
            key=lambda frame: similarities.get((section_index, query_index, int(frame.id)), 0.0),
            reverse=True,
        )
        matches.append(
            {
                "query_index": query_index,
                "operation_index": query_index,
                "operation_text": str(operation.get("operation_text") or query),
                "operation_start_seconds": operation.get("start_seconds"),
                "operation_end_seconds": operation.get("end_seconds"),
                "operation_start_time": operation.get("start_time"),
                "operation_end_time": operation.get("end_time"),
                "priority": operation.get("priority") or "medium",
                "source_segment_indices": operation.get("source_segment_indices") or [],
                "query_text": str(query or ""),
                "embedding_text": str(query or ""),
                "source": "visual_operations",
                "frame_queries_source": "visual_operations",
                "frames": [
                    {
                        **frame_ref(job.id, frame),
                        "similarity": similarities.get((section_index, query_index, int(frame.id)), 0.0),
                        "rank": rank,
                        "matched_query_index": query_index,
                        "matched_operation_index": query_index,
                        "matched_operation_text": str(operation.get("operation_text") or query),
                        "matched_query_text": str(query or ""),
                        "matched_query_source": "visual_operations",
                    }
                    for rank, frame in enumerate(ranked, start=1)
                ],
            }
        )
    return matches


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
