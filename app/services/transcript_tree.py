from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings
from app.models.entities import VideoFrame, VideoJob
from app.services.audit import finish_call, logged_call_with_session


RAW_ASR_KIND = "transcript_raw"
TRANSCRIPT_TREE_KIND = "transcript_tree"
TRANSCRIPT_RENDERED_KIND = "transcript_rendered"


@dataclass(frozen=True, slots=True)
class TranscriptBuildResult:
    raw_text: str
    tree: dict[str, Any]
    rendered_text: str


def build_structured_transcript(
    *,
    job: VideoJob,
    raw_text: str,
    raw_response: dict[str, Any] | None,
    frames: list[VideoFrame],
    wireframes: list[dict[str, Any]],
    duration_ms: int,
) -> TranscriptBuildResult:
    source_segments = extract_source_segments(raw_response or {}, raw_text=raw_text, duration_ms=duration_ms)
    semantic_tree = generate_semantic_tree(
        job=job,
        raw_text=raw_text,
        source_segments=source_segments,
        duration_ms=duration_ms,
    )
    tree = normalize_transcript_tree(
        tree=semantic_tree,
        job=job,
        raw_text=raw_text,
        source_segments=source_segments,
        frames=frames,
        wireframes=wireframes,
        duration_ms=duration_ms,
    )
    rendered_text = render_transcript_tree_text(tree)
    return TranscriptBuildResult(raw_text=raw_text, tree=tree, rendered_text=rendered_text)


def extract_source_segments(raw_response: dict[str, Any], *, raw_text: str, duration_ms: int) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    for key in ("segments", "chunks", "sentences"):
        value = raw_response.get(key)
        if isinstance(value, list):
            candidates = value
            break

    segments: list[dict[str, Any]] = []
    for index, item in enumerate(candidates):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("transcript") or item.get("sentence") or "").strip()
        if not text:
            continue
        start = parse_seconds(item.get("start") or item.get("start_seconds") or item.get("begin"))
        end = parse_seconds(item.get("end") or item.get("end_seconds") or item.get("finish"))
        if start is None and "timestamp" in item and isinstance(item["timestamp"], (list, tuple)) and len(item["timestamp"]) >= 2:
            start = parse_seconds(item["timestamp"][0])
            end = parse_seconds(item["timestamp"][1])
        segments.append(
            {
                "index": len(segments),
                "start_seconds": max(0.0, start or 0.0),
                "end_seconds": max(0.0, end or start or duration_ms / 1000 or 0.0),
                "text": text,
            }
        )

    if segments:
        return ensure_segment_ranges(segments, duration_ms=duration_ms)

    return []


def generate_semantic_tree(
    *,
    job: VideoJob,
    raw_text: str,
    source_segments: list[dict[str, Any]],
    duration_ms: int,
) -> dict[str, Any]:
    if not raw_text.strip():
        return fallback_semantic_tree(job=job, raw_text=raw_text, duration_ms=duration_ms)
    try:
        return QwenTranscriptTreeGenerator.from_settings().generate_tree(
            video_title=job.title or job.filename or job.id,
            filename=job.filename,
            duration=format_duration(duration_ms),
            raw_text=raw_text,
            source_segments=source_segments,
        )
    except Exception:
        return fallback_semantic_tree(job=job, raw_text=raw_text, duration_ms=duration_ms)


class QwenTranscriptTreeGenerator:
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

    @classmethod
    def from_settings(cls) -> "QwenTranscriptTreeGenerator":
        if not settings.qwen_text_api_key:
            raise ValueError("QWEN_TEXT_API_KEY is required for transcript structuring")
        return cls(
            api_key=settings.qwen_text_api_key,
            base_url=settings.qwen_text_base_url,
            model=settings.transcript_structure_model,
            temperature=settings.qwen_text_temperature,
            top_p=settings.qwen_text_top_p,
            max_tokens=min(settings.qwen_text_max_tokens, 12000),
            timeout_seconds=settings.qwen_text_timeout_seconds,
            max_input_chars=settings.qwen_text_max_input_chars,
        )

    def generate_tree(
        self,
        *,
        video_title: str,
        filename: str,
        duration: str,
        raw_text: str,
        source_segments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        segment_context = json.dumps(source_segments, ensure_ascii=False, indent=2)
        prompt = f"""请把下面的视频 ASR 原始转写整理成语义分段树。

目标结构：
- 一级标题是视频名称。
- 二级标题是语义段落标题，按主题、对象、动作、操作步骤或说明性质变化分段，不要按固定时间硬切。
- 三级内容是每段正文，并保留 start_seconds 和 end_seconds。

要求：
1. 不是摘要，不要压缩掉有效信息。
2. 可以修正明显口误、重复词和标点，但不要添加原文没有的信息。
3. 每段正文尽量保留原始 ASR 中的具体对象、参数、步骤、注意事项和风险点。
4. 每个二级标题 8 到 20 个字，优先使用“对象 + 动作/内容”的形式。
5. 分段要细到“一个清楚的操作步骤或说明单元”一级；如果视频包含连续操作，请按步骤拆分，不要把多个步骤粗合并成一段。
6. 每段必须填写 start_seconds 和 end_seconds。请根据完整 ASR 原文、视频时长和语义顺序合理估计时间范围，不要按固定时长硬切。
7. 不要判断关键帧价值，不要输出线框图推荐，不要输出 frame_id，不要输出图片路径。
8. 输出必须是 JSON，不要使用 Markdown，不要包裹代码块。
9. 如果“带时间范围的 ASR 片段”为空数组，仍然必须根据完整 ASR 全文和视频时长估计每个语义段落的 start_seconds/end_seconds。

视频名称：{video_title}
文件名：{filename}
视频时长：{duration}

带时间范围的 ASR 片段：
{clip_text(segment_context, self.max_input_chars // 2)}

原始 ASR 全文：
<<<RAW_ASR
{clip_text(raw_text, self.max_input_chars // 2)}
RAW_ASR

输出 JSON 格式：
{{
  "title": "视频名称",
  "sections": [
    {{
      "index": 1,
      "title": "语义段落标题",
      "start_seconds": 0,
      "end_seconds": 35,
      "text": "整理后的正文"
    }}
  ]
}}
"""
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是严谨的视频转写结构化助手。只基于原始 ASR 整理语义段落，输出可解析 JSON。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = f"{self._api_root()}/chat/completions"
        with logged_call_with_session(
            interface_type="model",
            tool_or_endpoint="qwen.chat.completions.transcript_tree",
            request={
                "model": self.model,
                "endpoint": url,
                "input_chars": len(prompt),
                "max_tokens": self.max_tokens,
            },
        ) as (audit_session, call_id):
            with httpx.Client(timeout=self.timeout_seconds, trust_env=False) as client:
                response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            content = extract_chat_content(data)
            parsed = parse_json_content(content)
            finish_call(
                audit_session,
                call_id,
                {
                    "model": self.model,
                    "usage": data.get("usage", {}),
                    "output_chars": len(content),
                    "sections": len(parsed.get("sections") or []),
                },
            )
            return parsed

    def _api_root(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url[: -len("/chat/completions")]
        return self.base_url


def normalize_transcript_tree(
    *,
    tree: dict[str, Any],
    job: VideoJob,
    raw_text: str,
    source_segments: list[dict[str, Any]],
    frames: list[VideoFrame],
    wireframes: list[dict[str, Any]],
    duration_ms: int,
) -> dict[str, Any]:
    title = str(tree.get("title") or job.title or job.filename or job.id).strip()
    raw_sections = tree.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        raw_sections = fallback_semantic_tree(job=job, raw_text=raw_text, duration_ms=duration_ms)["sections"]

    duration_seconds = round(duration_ms / 1000, 3) if duration_ms else 0.0
    sections = []
    for index, raw_section in enumerate(raw_sections, start=1):
        if not isinstance(raw_section, dict):
            continue
        section_title = str(raw_section.get("title") or f"语义段落 {index}").strip()
        text = str(raw_section.get("text") or "").strip()
        source_indices = normalize_source_indices(raw_section.get("source_segment_indices"))
        start = parse_seconds(raw_section.get("start_seconds"))
        end = parse_seconds(raw_section.get("end_seconds"))
        if start is None:
            start, segment_end = section_range_from_source_segments(source_segments, source_indices)
            if end is None:
                end = segment_end
        if end is None:
            segment_start, end = section_range_from_source_segments(source_segments, source_indices)
            if start is None:
                start = segment_start
        if start is None:
            start = proportional_time(index - 1, len(raw_sections), duration_seconds)
        if end is None or end <= start:
            end = proportional_time(index, len(raw_sections), duration_seconds)
        start = clamp_seconds(start, duration_seconds)
        end = clamp_seconds(max(end, start), duration_seconds)
        sections.append(
            {
                "index": len(sections) + 1,
                "title": section_title[:80],
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "start_time": format_timestamp(start),
                "end_time": format_timestamp(end),
                "text": text,
                "source_chunks": source_indices or matching_source_chunks(source_segments, start, end),
            }
        )

    if not sections:
        return normalize_transcript_tree(
            tree=fallback_semantic_tree(job=job, raw_text=raw_text, duration_ms=duration_ms),
            job=job,
            raw_text=raw_text,
            source_segments=source_segments,
            frames=frames,
            wireframes=wireframes,
            duration_ms=duration_ms,
        )

    sections = ensure_section_ranges(sections, duration_seconds)
    wireframe_lookup = map_wireframes_by_frame_ref(wireframes)
    for section in sections:
        section_frames = frames_for_section(
            video_id=job.id,
            frames=frames,
            wireframe_lookup=wireframe_lookup,
            start=float(section["start_seconds"]),
            end=float(section["end_seconds"]),
        )
        section["frames"] = section_frames
        section["wireframes"] = wireframes_from_frames(section_frames)

    return {
        "version": "2.2",
        "video": {
            "id": job.id,
            "title": title,
            "filename": job.filename,
            "duration_seconds": duration_seconds,
            "duration": format_duration(duration_ms),
        },
        "source": {
            "source_video_object_key": job.source_object_key,
            "source_video_url": f"/api/objects/{job.source_object_key}",
            "raw_asr_object_key": f"videos/{job.id}/transcript/raw_asr.txt",
            "model": settings.transcript_structure_model,
            "asr_model": settings.local_asr_model_label,
        },
        "tree": {
            "title": title,
            "sections": sections,
        },
    }


def render_transcript_tree_text(tree: dict[str, Any]) -> str:
    video = tree.get("video") or {}
    body = tree.get("tree") or {}
    title = body.get("title") or video.get("title") or "视频转写"
    lines = [f"# {title}", ""]
    for section in body.get("sections") or []:
        lines.extend(
            [
                f"## {section.get('index')}. {section.get('title')}",
                "",
                f"**时间范围：** {section.get('start_time')} - {section.get('end_time')}",
                "",
                "### 正文",
                "",
                str(section.get("text") or "").strip() or "未识别。",
                "",
                "### 关键帧",
                "",
            ]
        )
        frames = section.get("frames") or []
        if frames:
            for frame in frames:
                lines.append(f"- **{frame.get('timestamp_time')}** {frame.get('caption') or '关键帧'}")
                lines.append(f"  - 路径：{frame.get('object_key')}")
        else:
            lines.append("- 无")
        lines.extend(["", "### 线框图", ""])
        section_wireframes = section.get("wireframes") or wireframes_from_frames(frames)
        if section_wireframes:
            for wireframe in section_wireframes:
                lines.append(f"- **{wireframe.get('timestamp_time')}** 对应关键帧 {wireframe.get('frame_id')}")
                lines.append(f"  - 路径：{wireframe.get('object_key')}")
        else:
            lines.append("- 无")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def attach_media_to_transcript_tree(
    *,
    tree: dict[str, Any],
    video_id: str,
    frames: list[VideoFrame],
    wireframes: list[dict[str, Any]],
) -> dict[str, Any]:
    body = tree.setdefault("tree", {})
    sections = body.get("sections")
    if not isinstance(sections, list):
        return tree
    wireframe_lookup = map_wireframes_by_frame_ref(wireframes)
    for section in sections:
        if not isinstance(section, dict):
            continue
        start = parse_seconds(section.get("start_seconds"))
        end = parse_seconds(section.get("end_seconds"))
        if start is None:
            start = parse_seconds(section.get("start_time")) or 0.0
        if end is None:
            end = parse_seconds(section.get("end_time")) or start
        section_frames = frames_for_section(
            video_id=video_id,
            frames=frames,
            wireframe_lookup=wireframe_lookup,
            start=float(start),
            end=float(end),
        )
        section["frames"] = section_frames
        section["wireframes"] = wireframes_from_frames(section_frames)
    return tree


def build_markdown_from_transcript_tree(*, tree: dict[str, Any], source_video_object: str) -> str:
    video = tree.get("video") or {}
    body = tree.get("tree") or {}
    title = body.get("title") or video.get("title") or "视频分析结果"
    lines = [
        "---",
        'document_role: "video_analysis_result"',
        f'video_id: "{yaml_escape(str(video.get("id") or ""))}"',
        f'video_title: "{yaml_escape(str(title))}"',
        f'source_filename: "{yaml_escape(str(video.get("filename") or ""))}"',
        f'duration: "{yaml_escape(str(video.get("duration") or ""))}"',
        f'source_video_object: "{yaml_escape(source_video_object)}"',
        f'transcript_tree_object: "{yaml_escape(str(tree.get("source", {}).get("tree_object_key") or ""))}"',
        "---",
        "",
        f"# 视频分析结果：{title}",
        "",
    ]
    for section in body.get("sections") or []:
        frames = section.get("frames") or []
        section_wireframes = section.get("wireframes") or wireframes_from_frames(frames)
        lines.extend(
            [
                f"## {section.get('index')}. {section.get('title')}",
                "",
                f"**时间范围：** {section.get('start_time')} - {section.get('end_time')}",
                "",
                "### 正文",
                "",
                str(section.get("text") or "").strip() or "未识别。",
                "",
                "### 关键帧",
                "",
            ]
        )
        if frames:
            for frame in frames:
                lines.append(f"- **{frame.get('timestamp_time')}** {frame.get('caption') or '关键帧'}")
                if frame.get("object_key"):
                    lines.append(f"  - 路径：`{frame.get('object_key')}`")
                if frame.get("url"):
                    lines.append(f"  - 图片：![关键帧]({frame.get('url')})")
        else:
            lines.append("- 无")
        lines.extend(["", "### 线框图", ""])
        if section_wireframes:
            for wireframe in section_wireframes:
                lines.append(f"- **{wireframe.get('timestamp_time')}** 对应关键帧 {wireframe.get('frame_id')}")
                if wireframe.get("object_key"):
                    lines.append(f"  - 路径：`{wireframe.get('object_key')}`")
                if wireframe.get("url"):
                    lines.append(f"  - 图片：![线框图]({wireframe.get('url')})")
        else:
            lines.append("- 无")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def fallback_semantic_tree(*, job: VideoJob, raw_text: str, duration_ms: int) -> dict[str, Any]:
    text = raw_text.strip() or "未识别。"
    pieces = split_text_into_paragraphs(text)
    duration_seconds = round(duration_ms / 1000, 3) if duration_ms else 0.0
    sections = []
    for index, piece in enumerate(pieces, start=1):
        sections.append(
            {
                "index": index,
                "title": f"转写内容片段 {index}",
                "start_seconds": proportional_time(index - 1, len(pieces), duration_seconds),
                "end_seconds": proportional_time(index, len(pieces), duration_seconds),
                "text": piece,
            }
        )
    return {"title": job.title or job.filename or job.id, "sections": sections}


def split_text_into_paragraphs(text: str, *, target_chars: int = 450) -> list[str]:
    sentences = [item.strip() for item in re.split(r"(?<=[。！？!?；;])\s*", text) if item.strip()]
    if not sentences:
        return [text.strip()] if text.strip() else ["未识别。"]
    paragraphs: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) > target_chars:
            paragraphs.append(current)
            current = sentence
        else:
            current = f"{current}{sentence}" if current else sentence
    if current:
        paragraphs.append(current)
    return paragraphs or ["未识别。"]


def frames_for_section(
    *,
    video_id: str,
    frames: list[VideoFrame],
    wireframe_lookup: dict[str, dict[str, Any]],
    start: float,
    end: float,
) -> list[dict[str, Any]]:
    matched = []
    for frame in frames:
        timestamp = float(frame.timestamp_seconds or frame.timestamp_ms / 1000 or 0)
        if timestamp < start or timestamp > end:
            continue
        filename = frame.object_key.rsplit("/", 1)[-1]
        frame_stem = filename.rsplit(".", 1)[0]
        item: dict[str, Any] = {
            "id": frame.id,
            "timestamp_seconds": round(timestamp, 3),
            "timestamp_time": format_timestamp(timestamp),
            "caption": frame.caption or "",
            "object_key": frame.object_key,
            "url": f"/api/videos/{video_id}/frames/{filename}",
        }
        wireframe = wireframe_lookup.get(f"id:{frame.id}") or wireframe_lookup.get(f"stem:{frame_stem}")
        if wireframe is not None:
            item["wireframe"] = {
                "id": wireframe.get("id"),
                "object_key": wireframe.get("object_key"),
                "url": wireframe.get("url") or f"/api/objects/{wireframe.get('object_key')}",
                "media_type": wireframe.get("media_type"),
            }
        matched.append(item)
    return matched


def map_wireframes_by_frame_ref(wireframes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for artifact in wireframes:
        object_key = str(artifact.get("object_key") or "")
        if not object_key:
            continue
        stem = object_key.rsplit("/", 1)[-1].split(".", 1)[0]
        stem_key = f"stem:{stem}"
        if stem_key not in result:
            result[stem_key] = artifact
        explicit_frame_id = artifact.get("frame_id")
        if explicit_frame_id is not None:
            try:
                id_key = f"id:{int(explicit_frame_id)}"
            except (TypeError, ValueError):
                id_key = ""
            if id_key and id_key not in result:
                result[id_key] = artifact
        try:
            frame_id = int(stem)
        except ValueError:
            continue
        id_key = f"id:{frame_id}"
        if id_key not in result:
            result[id_key] = artifact
    return result


def wireframes_from_frames(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for frame in frames:
        wireframe = frame.get("wireframe")
        if not isinstance(wireframe, dict):
            continue
        result.append(
            {
                "id": wireframe.get("id"),
                "frame_id": frame.get("id"),
                "timestamp_seconds": frame.get("timestamp_seconds"),
                "timestamp_time": frame.get("timestamp_time"),
                "source_frame_object_key": frame.get("object_key"),
                "object_key": wireframe.get("object_key"),
                "url": wireframe.get("url"),
                "media_type": wireframe.get("media_type"),
            }
        )
    return result


def matching_source_chunks(source_segments: list[dict[str, Any]], start: float, end: float) -> list[int]:
    matched = []
    for segment in source_segments:
        segment_start = float(segment.get("start_seconds") or 0)
        segment_end = float(segment.get("end_seconds") or segment_start)
        if segment_end >= start and segment_start <= end:
            matched.append(int(segment.get("index") or 0))
    return matched


def normalize_source_indices(value: Any) -> list[int]:
    if value is None:
        return []
    raw_items = value if isinstance(value, list) else [value]
    indices = []
    for item in raw_items:
        try:
            index = int(item)
        except (TypeError, ValueError):
            continue
        if index not in indices:
            indices.append(index)
    return indices


def section_range_from_source_segments(source_segments: list[dict[str, Any]], indices: list[int]) -> tuple[float | None, float | None]:
    if not source_segments or not indices:
        return None, None
    by_index = {int(segment.get("index") or 0): segment for segment in source_segments}
    matched = [by_index[index] for index in indices if index in by_index]
    if not matched:
        return None, None
    start = min(float(segment.get("start_seconds") or 0) for segment in matched)
    end = max(float(segment.get("end_seconds") or segment.get("start_seconds") or 0) for segment in matched)
    return start, end


def ensure_segment_ranges(segments: list[dict[str, Any]], *, duration_ms: int) -> list[dict[str, Any]]:
    duration_seconds = round(duration_ms / 1000, 3) if duration_ms else 0.0
    normalized = []
    for index, segment in enumerate(segments):
        start = clamp_seconds(float(segment.get("start_seconds") or 0), duration_seconds)
        end = clamp_seconds(float(segment.get("end_seconds") or start), duration_seconds)
        if end <= start:
            end = proportional_time(index + 1, len(segments), duration_seconds)
        normalized.append({**segment, "index": index, "start_seconds": round(start, 3), "end_seconds": round(end, 3)})
    return normalized


def ensure_section_ranges(sections: list[dict[str, Any]], duration_seconds: float) -> list[dict[str, Any]]:
    previous_end = 0.0
    for index, section in enumerate(sections):
        start = float(section.get("start_seconds") or 0)
        end = float(section.get("end_seconds") or start)
        if index > 0 and start < previous_end:
            start = previous_end
        if end < start:
            end = start
        section["start_seconds"] = round(clamp_seconds(start, duration_seconds), 3)
        section["end_seconds"] = round(clamp_seconds(end, duration_seconds), 3)
        section["start_time"] = format_timestamp(float(section["start_seconds"]))
        section["end_time"] = format_timestamp(float(section["end_seconds"]))
        previous_end = float(section["end_seconds"])
    return sections


def parse_json_content(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    fence = re.match(r"\A```(?:json)?\s*(?P<body>.*?)\s*```\s*\Z", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group("body").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("Transcript structure response must be a JSON object")
    return parsed


def extract_chat_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]
            if isinstance(first.get("text"), str):
                return first["text"]
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    return ""


def parse_seconds(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 1000 if number > 100000 else number
    text = str(value).strip()
    if not text:
        return None
    if ":" in text:
        parts = [float(part) for part in text.split(":")]
        total = 0.0
        for part in parts:
            total = total * 60 + part
        return total
    try:
        return float(text)
    except ValueError:
        return None


def proportional_time(index: int, total: int, duration_seconds: float) -> float:
    if total <= 0 or duration_seconds <= 0:
        return 0.0
    return round(duration_seconds * index / total, 3)


def clamp_seconds(value: float, duration_seconds: float) -> float:
    if duration_seconds <= 0:
        return max(0.0, value)
    return max(0.0, min(duration_seconds, value))


def format_duration(duration_ms: int) -> str:
    if duration_ms <= 0:
        return "未知"
    return format_timestamp(duration_ms / 1000)


def format_timestamp(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def clip_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n\n[内容过长，已截断。]"


def yaml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
