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
    tree: dict[str, Any]
    rendered_text: str


def build_structured_transcript(
    *,
    job: VideoJob,
    raw_response: dict[str, Any] | None,
    frames: list[VideoFrame],
    wireframes: list[dict[str, Any]],
    duration_ms: int,
) -> TranscriptBuildResult:
    source_segments = extract_source_segments(raw_response or {}, duration_ms=duration_ms)
    semantic_tree = generate_semantic_tree(
        job=job,
        source_segments=source_segments,
        duration_ms=duration_ms,
    )
    tree = normalize_transcript_tree(
        tree=semantic_tree,
        job=job,
        source_segments=source_segments,
        frames=frames,
        wireframes=wireframes,
        duration_ms=duration_ms,
    )
    rendered_text = render_transcript_tree_text(tree)
    return TranscriptBuildResult(tree=tree, rendered_text=rendered_text)


def extract_source_segments(raw_response: dict[str, Any], *, duration_ms: int) -> list[dict[str, Any]]:
    candidates = raw_response.get("sentences")
    if not isinstance(candidates, list) or not candidates:
        raise RuntimeError("Local ASR did not return sentence-level timestamps")

    segments: list[dict[str, Any]] = []
    for index, item in enumerate(candidates):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("transcript") or item.get("sentence") or "").strip()
        if not text:
            continue
        start = parse_seconds(first_present(item, "start", "start_seconds", "begin"))
        end = parse_seconds(first_present(item, "end", "end_seconds", "finish"))
        if start is None and "timestamp" in item and isinstance(item["timestamp"], (list, tuple)) and len(item["timestamp"]) >= 2:
            start = parse_seconds(item["timestamp"][0])
            end = parse_seconds(item["timestamp"][1])
        if start is None or end is None:
            raise RuntimeError("Local ASR sentence is missing start/end timestamps")
        segments.append(
            {
                "index": len(segments),
                "start_seconds": max(0.0, start),
                "end_seconds": max(0.0, end),
                "text": text,
            }
        )

    if segments:
        return ensure_segment_ranges(segments, duration_ms=duration_ms)

    raise RuntimeError("Local ASR did not return usable sentence-level timestamps")


def generate_semantic_tree(
    *,
    job: VideoJob,
    source_segments: list[dict[str, Any]],
    duration_ms: int,
) -> dict[str, Any]:
    if not source_segments:
        raise RuntimeError("Local ASR did not return sentence-level timestamps")
    return QwenTranscriptTreeGenerator.from_settings().generate_tree(
        video_title=job.title or job.filename or job.id,
        filename=job.filename,
        duration=format_duration(duration_ms),
        source_segments=source_segments,
    )


def build_transcript_structure_prompt(
    *,
    video_title: str,
    filename: str,
    duration: str,
    source_segments: list[dict[str, Any]],
    max_input_chars: int,
    max_visual_operations_per_section: int,
) -> str:
    source_lines: list[str] = []
    for segment in source_segments:
        index = int(segment.get("index") or len(source_lines))
        start_time = format_timestamp(float(segment.get("start_seconds") or 0.0))
        end_time = format_timestamp(float(segment.get("end_seconds") or segment.get("start_seconds") or 0.0))
        text = str(segment.get("text") or "").strip()
        source_lines.append(f"[{index}] {start_time}-{end_time} {text}")

    source_context = "\n".join(source_lines) if source_lines else "(empty)"
    return f"""请把下面的 ASR 句子整理成自然、清晰的转写段落。
硬性规则：
1. source_sentences 中的每一项都是一个完整句子，不能把一个句子拆到两个段落里。
2. 每个段落必须输出 source_segment_indices，所有句子索引必须且只能出现一次。
3. 纯语气词句的索引仍需归入相邻段落，但正文可省略这些语气词。
4. 可以润色段落 text，并把技术参数里的口语数字规范化，例如“二百二十伏”写成“220伏”，“五十赫兹”写成“50Hz”。
5. 不要新增原文没有的信息，不要重排有实际语义的句子顺序。
6. 不要输出 start_seconds / end_seconds，段落时间由系统根据 source_segment_indices 和 ASR 句子时间计算。
7. 段落必须按原始句子顺序连续分组。
8. 按文本模型正常分段：围绕同一主题、同一讲解阶段、同一连续操作流程或同一结果说明的句子应合并成一个段落。
9. 不要为了一个小动作、一个工具变化、一个设备部件变化、一个参数变化或一个短暂画面状态单独拆段。
10. 只有当讲解主题、操作阶段、业务目的、场景对象或结论明显切换时，才拆成新的段落。
11. 不要一句话一个段落；除非该句是独立标题、独立结论或与前后内容明显无关，否则应与相邻句合并。
12. 段落粒度要适中，通常每段包含 2-6 个 ASR 句子；较长但连贯的同一阶段可以继续合并，避免把视频拆成 30 个以上的细碎段落。
13. 每个段落必须额外输出 visual_operations，用于描述本段中真正需要抽取关键帧的关键操作。
14. visual_operations 是数组，每个段落最多输出 {max(0, int(max_visual_operations_per_section or 0))} 个关键操作；可以输出空数组。
15. 不要输出所有动作，只输出“看到对应图片 + 读对应文字，就能更清楚知道这一步怎么操作”的视觉必要操作。
16. 相邻小动作如果能被同一张完成状态图覆盖，应合并成一个关键操作，不要拆成多个 visual_operations。
17. 每个 visual_operation 必须输出 operation_text、source_segment_indices、frame_query、priority。
18. operation_text 是给人读的润色操作句，保留原文操作含义，不要编造 ASR 原文没有出现的设备、工具、部件、参数或动作。
19. visual_operation.source_segment_indices 必须来自该段落的 source_segment_indices，必须按原始句子顺序连续分组，不能把一个 ASR 句子拆到两个关键操作里。
20. visual_operations 必须按原文句子顺序输出，不要把靠后的操作排到靠前操作之前。
21. frame_query 用于图片-文字 embedding 相似度匹配，应描述“最适合作为最终业务帧入选”的画面，而不是单纯描述正在发生的动作过程。
22. frame_query 应优先描述动作完成后或关键状态稳定时的画面，例如器件安装完成、连接位置清楚、读数可见、测试结果状态可辨认。
23. frame_query 的优先级依次是：主体设备或关键器件清晰可见、操作位置无遮挡、状态或结果可辨认、画面适合转成线框图、工具或手部只作为辅助证据。
24. 当“正在操作的动作帧”和“操作完成后的清晰状态帧”都能代表该关键操作时，优先生成指向清晰状态帧的 frame_query。
25. 只有当手部动作是判断该业务操作的必要证据时，才描述手部动作；即使需要描述，也必须让主体设备、关键器件或连接位置作为画面主体。
26. 不要生成会诱导匹配手部特写、手臂遮挡、人体占主体的 query；不要使用“手部特写”“手臂特写”“人体特写”“正在用手操作的特写”等表达。
27. 对装配、拧紧、插接、按压、测试等操作，尽量改写为“完成后的安装状态、连接状态、部件位置、读数或测试状态清晰可见”的画面描述，少写“正在拧、正在按、正在拿、正在活动”等过程动作。
28. frame_query 只写画面中可能直接看见的内容，不写原因、目的、背景、注意事项、风险解释、规范要求、抽象总结。
29. 不写“本段介绍”“视频中讲到”“需要注意”“应该确保”等讲解性表达。
30. 每条 frame_query 长度控制在 20-80 个中文字符。
视频名称：{video_title}
文件名：{filename}
视频时长：{duration}

source_sentences:
{clip_text(source_context, max_input_chars // 2)}

输出 JSON：
{{
  "title": "视频名称",
  "sections": [
    {{
      "index": 1,
      "title": "语义段落标题",
      "source_segment_indices": [0, 1],
      "text": "整理后的正文",
      "visual_operations": [
        {{
          "index": 1,
          "operation_text": "润色后的关键操作步骤",
          "source_segment_indices": [0],
          "priority": "high",
          "frame_query": "适合图片文字相似度匹配的关键业务帧画面描述"
        }}
      ]
    }}
  ]
}}
"""


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
        source_segments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        prompt = build_transcript_structure_prompt(
            video_title=video_title,
            filename=filename,
            duration=duration,
            source_segments=source_segments,
            max_input_chars=self.max_input_chars,
            max_visual_operations_per_section=settings.video_max_visual_operations_per_section,
        )
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是严谨的视频转写结构化助手，只基于原始 ASR 句子归并语义段落，输出可解析 JSON。",
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
    source_segments: list[dict[str, Any]],
    frames: list[VideoFrame],
    wireframes: list[dict[str, Any]],
    duration_ms: int,
) -> dict[str, Any]:
    title = str(tree.get("title") or job.title or job.filename or job.id).strip()
    raw_sections = tree.get("sections")
    if not source_segments:
        raise RuntimeError("Local ASR did not return sentence-level timestamps")
    if not isinstance(raw_sections, list) or not raw_sections:
        raise RuntimeError("Qwen transcript structuring did not return sections")

    duration_seconds = round(duration_ms / 1000, 3) if duration_ms else 0.0
    sections = []
    source_timing_required = bool(source_segments)
    expected_source_indices = {int(segment.get("index") or 0) for segment in source_segments}
    assigned_source_indices: set[int] = set()

    for index, raw_section in enumerate(raw_sections, start=1):
        if not isinstance(raw_section, dict):
            continue
        section_title = str(raw_section.get("title") or f"语义段落 {index}").strip()
        text = str(raw_section.get("text") or "").strip()
        source_indices = normalize_source_indices(raw_section.get("source_segment_indices") or raw_section.get("source_chunks"))
        source_indices = sorted(source_indices)
        if not source_indices or not source_indices_are_contiguous(source_indices):
            raise RuntimeError("Qwen transcript structuring must return contiguous source_segment_indices")
        if any(source_index in assigned_source_indices for source_index in source_indices):
            raise RuntimeError("Qwen transcript structuring reused an ASR sentence index")
        start, end = section_range_from_source_segments(source_segments, source_indices)
        if start is None or end is None:
            raise RuntimeError("ASR sentence timestamps are missing for one or more transcript sections")
        assigned_source_indices.update(source_indices)
        start = clamp_seconds(start, duration_seconds)
        end = clamp_seconds(max(end, start), duration_seconds)
        visual_operations = normalize_visual_operations(
            raw_section.get("visual_operations") or raw_section.get("key_operations") or [],
            source_segments=source_segments,
            section_source_indices=source_indices,
            duration_seconds=duration_seconds,
            max_operations=settings.video_max_visual_operations_per_section,
        )
        frame_queries = [
            operation["frame_query"]
            for operation in visual_operations
            if str(operation.get("frame_query") or "").strip()
        ]
        sections.append(
            {
                "index": len(sections) + 1,
                "title": section_title[:80],
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "start_time": format_timestamp(start),
                "end_time": format_timestamp(end),
                "text": text,
                "frame_queries": frame_queries,
                "frame_queries_source": "visual_operations",
                "visual_operations": visual_operations,
                "source_chunks": source_indices or matching_source_chunks(source_segments, start, end),
            }
        )

    if source_timing_required and assigned_source_indices != expected_source_indices:
        raise RuntimeError("Qwen transcript structuring did not cover every ASR sentence")

    if not sections:
        raise RuntimeError("Qwen transcript structuring did not produce any transcript sections")

    sections = ensure_section_ranges(sections, duration_seconds)
    if frames or wireframes:
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
            ]
        )
        visual_operations = section.get("visual_operations") if isinstance(section.get("visual_operations"), list) else []
        if visual_operations:
            lines.extend(["### 关键操作", ""])
            for operation in visual_operations:
                lines.append(
                    f"- {operation.get('index')}. {operation.get('operation_text') or operation.get('frame_query') or '关键操作'}"
                )
                if operation.get("start_time") and operation.get("end_time"):
                    lines.append(f"  - 时间范围：{operation.get('start_time')} - {operation.get('end_time')}")
                if operation.get("frame_query"):
                    lines.append(f"  - frame_query：{operation.get('frame_query')}")
            lines.append("")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def normalize_visual_operations(
    value: Any,
    *,
    source_segments: list[dict[str, Any]],
    section_source_indices: list[int],
    duration_seconds: float,
    max_operations: int,
) -> list[dict[str, Any]]:
    if max_operations <= 0:
        return []
    raw_items = value if isinstance(value, list) else []
    section_index_set = set(section_source_indices)
    assigned_indices: set[int] = set()
    operations: list[dict[str, Any]] = []
    last_source_index = -1

    for raw_item in raw_items:
        if len(operations) >= max_operations:
            break
        if not isinstance(raw_item, dict):
            continue
        source_indices = normalize_source_indices(raw_item.get("source_segment_indices") or raw_item.get("source_chunks"))
        source_indices = sorted(source_indices)
        if not source_indices:
            raise RuntimeError("Qwen visual operation must return source_segment_indices")
        if not source_indices_are_contiguous(source_indices):
            raise RuntimeError("Qwen visual operation source_segment_indices must be contiguous")
        if any(source_index not in section_index_set for source_index in source_indices):
            raise RuntimeError("Qwen visual operation source_segment_indices must belong to its transcript section")
        if any(source_index in assigned_indices for source_index in source_indices):
            raise RuntimeError("Qwen visual operation reused an ASR sentence index")
        if source_indices[0] <= last_source_index:
            raise RuntimeError("Qwen visual operations must be ordered by ASR sentence index")

        start, end = section_range_from_source_segments(source_segments, source_indices)
        if start is None or end is None:
            raise RuntimeError("ASR sentence timestamps are missing for one or more visual operations")
        start = clamp_seconds(start, duration_seconds)
        end = clamp_seconds(max(end, start), duration_seconds)
        operation_text = re.sub(
            r"\s+",
            " ",
            str(raw_item.get("operation_text") or raw_item.get("text") or raw_item.get("title") or "").strip(),
        ).strip()
        frame_query = re.sub(r"\s+", " ", str(raw_item.get("frame_query") or "").strip()).strip()
        if not frame_query:
            raise RuntimeError("Qwen visual operation must return frame_query")
        priority = str(raw_item.get("priority") or "medium").strip().lower()
        if priority not in {"high", "medium", "low"}:
            priority = "medium"

        assigned_indices.update(source_indices)
        last_source_index = source_indices[-1]
        operations.append(
            {
                "index": len(operations) + 1,
                "operation_text": (operation_text or frame_query)[:160],
                "source_segment_indices": source_indices,
                "priority": priority,
                "frame_query": frame_query[:160],
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "start_time": format_timestamp(start),
                "end_time": format_timestamp(end),
            }
        )

    return operations


def normalize_frame_queries(value: Any, *, section_title: str, text: str) -> list[str]:
    raw_items = value if isinstance(value, list) else [value]
    queries: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        query = re.sub(r"\s+", " ", str(item or "")).strip()
        if not query or query in seen:
            continue
        queries.append(query[:160])
        seen.add(query)
        if len(queries) >= 3:
            break

    if queries:
        return queries

    fallback = re.sub(r"\s+", " ", "，".join(item for item in [section_title, text] if item)).strip()
    return [fallback[:160]] if fallback else []


def infer_frame_queries_source(value: Any, *, frame_queries: list[str], section_title: str, text: str) -> str:
    raw_items = value if isinstance(value, list) else [value]
    has_raw_query = any(str(item or "").strip() for item in raw_items)
    fallback = re.sub(r"\s+", " ", "，".join(item for item in [section_title, text] if item)).strip()[:160]
    if not has_raw_query:
        return "fallback_title_text"
    if fallback and len(frame_queries) == 1 and frame_queries[0] == fallback:
        return "fallback_title_text"
    return "model"


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
                    lines.append(f"  - 路径：{frame.get('object_key')}")
                if frame.get("url"):
                    lines.append(f"  - 图片：[关键帧]({frame.get('url')})")
        else:
            lines.append("- 无")

        lines.extend(["", "### 线框图", ""])
        if section_wireframes:
            for wireframe in section_wireframes:
                lines.append(f"- **{wireframe.get('timestamp_time')}** 对应关键帧 {wireframe.get('frame_id')}")
                if wireframe.get("object_key"):
                    lines.append(f"  - 路径：{wireframe.get('object_key')}")
                if wireframe.get("url"):
                    lines.append(f"  - 图片：[线框图]({wireframe.get('url')})")
        else:
            lines.append("- 无")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def source_indices_are_contiguous(indices: list[int]) -> bool:
    if not indices:
        return False
    ordered = sorted(indices)
    return ordered == list(range(ordered[0], ordered[-1] + 1))


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
            raise RuntimeError("Local ASR sentence timestamps must have end greater than start")
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


def first_present(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


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


