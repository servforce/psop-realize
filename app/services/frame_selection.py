from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import VideoFrame, VideoJob
from app.services.audit import finish_call, logged_call_with_session
from app.services.storage import storage_service
from app.services.transcript_tree import clip_text, extract_chat_content, parse_json_content
from app.services.video_outputs import transcript_tree_object_key


SELECTED = "selected"
REJECTED = "rejected"
PENDING = "pending"


@dataclass(slots=True)
class QualityResult:
    passed: bool
    reason: str
    details: dict[str, Any]


@dataclass(slots=True)
class FrameCandidate:
    frame: VideoFrame
    image_bytes: bytes
    quality: QualityResult


def ensure_frame_selection(session: Session, video_id: str) -> list[VideoFrame]:
    frames = list(
        session.scalars(
            select(VideoFrame).where(VideoFrame.video_id == video_id).order_by(VideoFrame.timestamp_ms.asc())
        )
    )
    if not frames:
        return []

    if not settings.video_frame_selection_enabled:
        for frame in frames:
            if frame.selection_status == PENDING:
                mark_frame_selected(
                    frame,
                    score=1.0,
                    reason="Frame selection is disabled; selected by compatibility fallback.",
                    details={"selection_enabled": False},
                    matched_section_index=None,
                )
        enforce_selection_limit(frames)
        session.commit()
        return selected_frames(frames)

    if all((frame.selection_status or PENDING) in {SELECTED, REJECTED} for frame in frames):
        enforce_selection_limit(frames)
        session.commit()
        return selected_frames(frames)

    job = session.get(VideoJob, video_id)
    transcript_tree = load_transcript_tree(session, video_id)
    sections = transcript_sections_for_prompt(transcript_tree)

    candidates: list[FrameCandidate] = []
    for frame in frames:
        image_bytes = storage_service.get_bytes(bucket=frame.bucket, object_key=frame.object_key)
        quality = analyze_image_quality(image_bytes)
        if not quality.passed:
            mark_frame_rejected(
                frame,
                score=0.0,
                reason=quality.reason,
                details={"quality": quality.details, "stage": "quality_filter"},
                matched_section_index=None,
            )
            continue
        candidates.append(FrameCandidate(frame=frame, image_bytes=image_bytes, quality=quality))

    if candidates:
        selector = QwenVlFrameSelector.from_settings()
        if selector.enabled:
            selector.select_frames(
                job=job,
                sections=sections,
                candidates=candidates,
            )
        else:
            for candidate in candidates:
                mark_frame_selected(
                    candidate.frame,
                    score=0.5,
                    reason="Qwen VL frame selection is not configured; selected after passing local quality checks.",
                    details={
                        "quality": candidate.quality.details,
                        "stage": "qwen_vl_not_configured",
                    },
                    matched_section_index=None,
                )

    if not any(frame.selected_for_wireframe for frame in frames):
        fallback = best_quality_candidate(candidates)
        if fallback is not None:
            mark_frame_selected(
                fallback.frame,
                score=0.51,
                reason="No frame was selected by the model; kept the best quality candidate conservatively.",
                details={
                    "quality": fallback.quality.details,
                    "stage": "no_model_selection_fallback",
                },
                matched_section_index=None,
            )

    enforce_selection_limit(frames)
    session.commit()
    return selected_frames(frames)


def selected_frames(frames: list[VideoFrame]) -> list[VideoFrame]:
    return [frame for frame in frames if bool(frame.selected_for_wireframe)]


def best_quality_candidate(candidates: list[FrameCandidate]) -> FrameCandidate | None:
    if not candidates:
        return None
    return max(candidates, key=lambda item: float(item.quality.details.get("blur_score") or 0.0))


def enforce_selection_limit(frames: list[VideoFrame]) -> None:
    max_selected = int(settings.video_frame_selection_max_selected_frames or 0)
    if max_selected <= 0:
        return
    selected = selected_frames(frames)
    if len(selected) <= max_selected:
        return

    ranked = sorted(
        selected,
        key=lambda frame: (
            -float(frame.selection_score or 0.0),
            int(frame.timestamp_ms or 0),
            int(frame.id or 0),
        ),
    )
    kept_ids = {frame.id for frame in ranked[:max_selected]}
    for frame in selected:
        if frame.id in kept_ids:
            continue
        previous_details = parse_json_object(frame.selection_details_json)
        previous_details["final_limit"] = {
            "max_selected_frames": max_selected,
            "selected_before_limit": len(selected),
            "stage": "final_selection_limit",
        }
        mark_frame_rejected(
            frame,
            score=float(frame.selection_score or 0.0),
            reason=(
                f"Rejected by final frame selection limit: kept the top {max_selected} "
                "selected frames by model score."
            ),
            details=previous_details,
            matched_section_index=frame.matched_section_index,
        )


def mark_frame_selected(
    frame: VideoFrame,
    *,
    score: float,
    reason: str,
    details: dict[str, Any],
    matched_section_index: int | None,
) -> None:
    frame.selected_for_wireframe = True
    frame.selection_status = SELECTED
    frame.selection_score = max(0.0, min(1.0, float(score)))
    frame.selection_reason = reason[:4000]
    frame.selection_details_json = json.dumps(details, ensure_ascii=False, default=str)
    frame.matched_section_index = matched_section_index


def mark_frame_rejected(
    frame: VideoFrame,
    *,
    score: float,
    reason: str,
    details: dict[str, Any],
    matched_section_index: int | None,
) -> None:
    frame.selected_for_wireframe = False
    frame.selection_status = REJECTED
    frame.selection_score = max(0.0, min(1.0, float(score)))
    frame.selection_reason = reason[:4000]
    frame.selection_details_json = json.dumps(details, ensure_ascii=False, default=str)
    frame.matched_section_index = matched_section_index


def analyze_image_quality(image_bytes: bytes) -> QualityResult:
    try:
        details = analyze_quality_with_opencv(image_bytes)
    except Exception:
        details = analyze_quality_with_pillow(image_bytes)

    blur_score = float(details.get("blur_score") or 0.0)
    mean_brightness = float(details.get("mean_brightness") or 0.0)
    dark_ratio = float(details.get("dark_ratio") or 0.0)
    bright_ratio = float(details.get("bright_ratio") or 0.0)

    failures: list[str] = []
    if blur_score < settings.video_frame_selection_min_blur_score:
        failures.append("severe_blur")
    if dark_ratio >= settings.video_frame_selection_dark_ratio_threshold or mean_brightness <= settings.video_frame_selection_min_mean_brightness:
        failures.append("too_dark_or_black")
    if bright_ratio >= settings.video_frame_selection_bright_ratio_threshold or mean_brightness >= settings.video_frame_selection_max_mean_brightness:
        failures.append("too_bright_or_overexposed")

    details["failures"] = failures
    if failures:
        return QualityResult(
            passed=False,
            reason=f"Rejected by local quality filter: {', '.join(failures)}.",
            details=details,
        )
    return QualityResult(
        passed=True,
        reason="Passed local quality checks.",
        details=details,
    )


def analyze_quality_with_opencv(image_bytes: bytes) -> dict[str, Any]:
    import cv2
    import numpy as np

    buffer = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("OpenCV failed to decode image")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return {
        "method": "opencv_laplacian",
        "width": int(image.shape[1]),
        "height": int(image.shape[0]),
        "blur_score": float(cv2.Laplacian(gray, cv2.CV_64F).var()),
        "mean_brightness": float(gray.mean()),
        "dark_ratio": float((gray < 20).mean()),
        "bright_ratio": float((gray > 245).mean()),
    }


def analyze_quality_with_pillow(image_bytes: bytes) -> dict[str, Any]:
    from PIL import Image, ImageFilter, ImageStat

    with Image.open(io.BytesIO(image_bytes)) as image:
        gray = image.convert("L")
        histogram = gray.histogram()
        total = max(1, sum(histogram))
        mean_brightness = float(ImageStat.Stat(gray).mean[0])
        edge = gray.filter(ImageFilter.FIND_EDGES)
        blur_score = float(ImageStat.Stat(edge).var[0])
        return {
            "method": "pillow_edge_fallback",
            "width": int(image.width),
            "height": int(image.height),
            "blur_score": blur_score,
            "mean_brightness": mean_brightness,
            "dark_ratio": sum(histogram[:20]) / total,
            "bright_ratio": sum(histogram[246:]) / total,
        }


def load_transcript_tree(session: Session, video_id: str) -> dict[str, Any] | None:
    job = session.get(VideoJob, video_id)
    if job is None:
        return None
    try:
        return json.loads(
            storage_service.get_bytes(bucket=job.source_bucket, object_key=transcript_tree_object_key(video_id)).decode(
                "utf-8",
                errors="replace",
            )
        )
    except Exception:
        return None


def transcript_sections_for_prompt(tree: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(tree, dict):
        return []
    body = tree.get("tree") or {}
    sections = body.get("sections") or []
    result: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        result.append(
            {
                "index": section.get("index"),
                "title": str(section.get("title") or "")[:120],
                "text": clip_text(str(section.get("text") or ""), 1200),
                "start_seconds": section.get("start_seconds"),
                "end_seconds": section.get("end_seconds"),
                "time_note": "time range is only a weak reference and may be inaccurate",
            }
        )
    return result


class QwenVlFrameSelector:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float,
        top_p: float,
        max_tokens: int,
        timeout_seconds: float,
        max_input_chars: int,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.top_p = top_p
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.max_input_chars = max_input_chars

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.model)

    @classmethod
    def from_settings(cls) -> "QwenVlFrameSelector":
        return cls(
            api_key=settings.qwen_vl_api_key,
            base_url=settings.qwen_vl_base_url,
            model=settings.qwen_vl_model,
            temperature=settings.qwen_vl_temperature,
            top_p=settings.qwen_vl_top_p,
            max_tokens=settings.qwen_vl_max_tokens,
            timeout_seconds=settings.qwen_vl_timeout_seconds,
            max_input_chars=settings.qwen_vl_max_input_chars,
        )

    def select_frames(
        self,
        *,
        job: VideoJob | None,
        sections: list[dict[str, Any]],
        candidates: list[FrameCandidate],
    ) -> None:
        group_size = max(1, min(12, settings.video_frame_selection_group_size))
        for group_index, group in enumerate(chunked(candidates, group_size), start=1):
            parsed = self._select_group(job=job, sections=sections, candidates=group, group_index=group_index)
            apply_model_selection(group=group, parsed=parsed, model=self.model, group_index=group_index)

    def _select_group(
        self,
        *,
        job: VideoJob | None,
        sections: list[dict[str, Any]],
        candidates: list[FrameCandidate],
        group_index: int,
    ) -> dict[str, Any]:
        frame_metadata = [
            {
                "frame_id": candidate.frame.id,
                "timestamp_seconds": round(float(candidate.frame.timestamp_seconds or candidate.frame.timestamp_ms / 1000 or 0), 3),
                "object_key": candidate.frame.object_key,
                "quality": candidate.quality.details,
            }
            for candidate in candidates
        ]
        context = {
            "video": {
                "id": job.id if job is not None else "",
                "title": job.title if job is not None else "",
                "filename": job.filename if job is not None else "",
                "duration_seconds": round((job.duration_ms or 0) / 1000, 3) if job is not None else 0,
            },
            "transcript_sections": sections,
            "candidate_frames": frame_metadata,
            "matching_mode": "global_semantic_matching; section times are weak references and may be inaccurate",
        }
        prompt = build_selection_prompt(context, max_chars=self.max_input_chars)
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for candidate in candidates:
            content.append(
                {
                    "type": "text",
                    "text": f"frame_id={candidate.frame.id}, timestamp_seconds={candidate.frame.timestamp_seconds}",
                }
            )
            content.append({"type": "image_url", "image_url": {"url": image_data_url(candidate.image_bytes)}})

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You select business evidence frames for technical work videos. Return strict JSON only.",
                },
                {"role": "user", "content": content},
            ],
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = f"{self._api_root()}/chat/completions"
        with logged_call_with_session(
            interface_type="model",
            tool_or_endpoint="qwen.chat.completions.frame_selection",
            request={
                "model": self.model,
                "endpoint": url,
                "group_index": group_index,
                "frame_count": len(candidates),
                "section_count": len(sections),
            },
        ) as (audit_session, call_id):
            with httpx.Client(timeout=self.timeout_seconds, trust_env=False) as client:
                response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            content_text = extract_chat_content(data)
            parsed = parse_json_content(content_text)
            finish_call(
                audit_session,
                call_id,
                {
                    "model": self.model,
                    "usage": data.get("usage", {}),
                    "group_index": group_index,
                    "frame_count": len(candidates),
                    "output_chars": len(content_text),
                    "selected_count": count_selected(parsed),
                },
            )
            return parsed

    def _api_root(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url[: -len("/chat/completions")]
        return self.base_url


def build_selection_prompt(context: dict[str, Any], *, max_chars: int) -> str:
    context_text = clip_text(json.dumps(context, ensure_ascii=False, indent=2), max_chars)
    return f"""请从候选关键帧中筛选“适合生成线框图的硬件作业证据帧”。

重要定义：
- 目标不是选择最好看的图片，也不是普通视频摘要。
- 目标是选择最能支撑实际硬件作业步骤、技术说明、检查确认、接线/接口/部件状态、工具使用、实物标识或设备读数的帧。
- 合格帧通常必须能看到真实硬件部件、接口、端子、接线、工具接触部件、手部正在操作部件，或操作完成后的硬件状态。
- 只有书页、说明书、图纸、表格、封面、PPT、字幕、泛泛文字说明的画面，即使文字清晰，也不要选择；除非同一帧中同时清楚展示了正在被操作或检查的真实硬件部件。
- 如果同一操作既有“正在操作中”的帧，也有“操作完成后状态清晰”的帧，请优先给完成状态清晰、部件无遮挡、接线/接口结果可确认的帧更高分。
- 当前语义分段的 start_seconds/end_seconds 可能不可靠，只能作为弱参考；请主要根据画面内容与 transcript_sections 的语义相关性做全局匹配。

筛选规则：
1. 如果帧能清楚展示真实硬件对象、工具与部件接触、端子/接口/接线状态、设备面板读数、实物铭牌，或操作完成后的部件状态，优先选择。
2. 如果帧只是书页、说明书、图纸、表格、封面、PPT、字幕、过渡画面、空镜头、人物走动、主体不清、看不到真实作业对象，必须不选择。
3. 如果画面中有硬件但被手、工具、模糊、过曝、遮挡严重影响确认，应降低分数；如果同组有更清晰的完成状态帧，应不选择较差帧。
4. 不要因为多张帧画面相似就自动删除；如果它们体现不同硬件步骤或不同完成状态，可以都选择。
5. 每个候选 frame_id 都必须在输出 frames 数组中出现一次。

请输出严格 JSON，不要 Markdown，不要代码块。格式：
{{
  "frames": [
    {{
      "frame_id": 123,
      "selected_for_wireframe": true,
      "importance_score": 0.86,
      "matched_section_index": 2,
      "matched_section_title": "语义段落标题",
      "reason": "说明这张帧为什么适合或不适合生成线框图",
      "hardware_component_present": true,
      "operation_state": "in_progress_or_completed",
      "visual_evidence": ["真实硬件部件", "工具", "接线/接口", "实物标识/设备读数", "动作或完成状态"],
      "wireframe_value": "high"
    }}
  ]
}}

上下文：
{context_text}
"""


def apply_model_selection(
    *,
    group: list[FrameCandidate],
    parsed: dict[str, Any],
    model: str,
    group_index: int,
) -> None:
    raw_items = parsed.get("frames") if isinstance(parsed, dict) else []
    by_frame_id: dict[int, dict[str, Any]] = {}
    if isinstance(raw_items, list):
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            try:
                frame_id = int(item.get("frame_id"))
            except (TypeError, ValueError):
                continue
            by_frame_id[frame_id] = item

    for candidate in group:
        item = by_frame_id.get(candidate.frame.id)
        if item is None:
            mark_frame_selected(
                candidate.frame,
                score=0.5,
                reason="Qwen VL did not return this frame; selected conservatively after local quality checks.",
                details={
                    "quality": candidate.quality.details,
                    "model": model,
                    "group_index": group_index,
                    "stage": "missing_model_result_fallback",
                },
                matched_section_index=None,
            )
            continue

        selected = bool(item.get("selected_for_wireframe"))
        score = parse_score(item.get("importance_score"), default=0.5 if selected else 0.0)
        matched_section_index = parse_optional_int(item.get("matched_section_index"))
        reason = str(item.get("reason") or ("Selected by Qwen VL." if selected else "Rejected by Qwen VL."))
        details = {
            "quality": candidate.quality.details,
            "model": model,
            "group_index": group_index,
            "matched_section_index": matched_section_index,
            "matched_section_title": item.get("matched_section_title"),
            "visual_evidence": item.get("visual_evidence") if isinstance(item.get("visual_evidence"), list) else [],
            "wireframe_value": item.get("wireframe_value"),
            "raw_model_item": item,
        }
        if selected:
            mark_frame_selected(
                candidate.frame,
                score=score,
                reason=reason,
                details=details,
                matched_section_index=matched_section_index,
            )
        else:
            mark_frame_rejected(
                candidate.frame,
                score=score,
                reason=reason,
                details=details,
                matched_section_index=matched_section_index,
            )


def image_data_url(image_bytes: bytes) -> str:
    content = compress_image_for_vl(image_bytes)
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def compress_image_for_vl(image_bytes: bytes) -> bytes:
    from PIL import Image

    max_side = max(256, settings.video_frame_selection_image_max_side)
    quality = max(40, min(95, settings.video_frame_selection_jpeg_quality))
    with Image.open(io.BytesIO(image_bytes)) as image:
        image = image.convert("RGB")
        image.thumbnail((max_side, max_side))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=quality, optimize=True)
        return output.getvalue()


def count_selected(parsed: dict[str, Any]) -> int:
    frames = parsed.get("frames") if isinstance(parsed, dict) else []
    if not isinstance(frames, list):
        return 0
    return sum(1 for item in frames if isinstance(item, dict) and bool(item.get("selected_for_wireframe")))


def parse_score(value: Any, *, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def parse_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def chunked(items: list[FrameCandidate], size: int) -> list[list[FrameCandidate]]:
    return [items[index : index + size] for index in range(0, len(items), size)]
