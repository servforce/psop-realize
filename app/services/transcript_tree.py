from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from app.core.video_config import video_settings as settings
from app.models.video import VideoFrame, VideoJob
from app.services.audit import finish_call, logged_call_with_session
from app.services.query_graph import QueryGraphError, normalize_query_graph
from app.services.video_outputs import analysis_proxy_video_object_key


RAW_ASR_KIND = "transcript_raw"
TRANSCRIPT_TREE_KIND = "transcript_tree"
RAW_ASR_GENERATION_VERSION = "1"
STRUCTURED_TRANSCRIPT_GENERATION_VERSION = "8"
STRUCTURED_TRANSCRIPT_MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class TranscriptBuildResult:
    tree: dict[str, Any]
    rendered_text: str


def build_transcript_raw_generation_info(*, job: VideoJob) -> dict[str, Any]:
    return {
        "kind": RAW_ASR_KIND,
        "version": RAW_ASR_GENERATION_VERSION,
        "source_object_key": job.source_object_key,
        "analysis_proxy_object_key": analysis_proxy_video_object_key(job.id),
        "local_asr_model_label": settings.local_asr_model_label,
        "local_asr_language": settings.local_asr_language,
        "video_analysis_proxy_height": int(settings.video_analysis_proxy_height),
        "video_analysis_proxy_crf": int(settings.video_analysis_proxy_crf),
        "video_analysis_proxy_preset": settings.video_analysis_proxy_preset,
        "video_analysis_proxy_audio_bitrate": settings.video_analysis_proxy_audio_bitrate,
    }


def build_transcript_structure_generation_info(*, job: VideoJob) -> dict[str, Any]:
    return {
        "kind": TRANSCRIPT_TREE_KIND,
        "version": STRUCTURED_TRANSCRIPT_GENERATION_VERSION,
        "raw": build_transcript_raw_generation_info(job=job),
        "transcript_structure_model": settings.transcript_structure_model,
        "transcript_business_frame_mode": "section_polished_text_business_frame_text_and_query_graph_v1",
    }


def attach_transcript_generation_metadata(*, tree: dict[str, Any], job: VideoJob) -> dict[str, Any]:
    source = tree.setdefault("source", {})
    if isinstance(source, dict):
        source["generation"] = build_transcript_structure_generation_info(job=job)
    return tree


def build_structured_transcript(
    *,
    job: VideoJob,
    raw_response: dict[str, Any] | None,
    frames: list[VideoFrame],
    wireframes: list[dict[str, Any]],
    duration_ms: int,
) -> TranscriptBuildResult:
    source_segments = extract_source_segments(raw_response or {}, duration_ms=duration_ms)
    retry_feedback = ""
    last_error: RuntimeError | None = None
    for attempt in range(1, STRUCTURED_TRANSCRIPT_MAX_ATTEMPTS + 1):
        semantic_tree = generate_semantic_tree(
            job=job,
            source_segments=source_segments,
            duration_ms=duration_ms,
            retry_feedback=retry_feedback,
        )
        try:
            tree = normalize_transcript_tree(
                tree=semantic_tree,
                job=job,
                source_segments=source_segments,
                frames=frames,
                wireframes=wireframes,
                duration_ms=duration_ms,
            )
            break
        except RuntimeError as exc:
            last_error = exc
            if attempt >= STRUCTURED_TRANSCRIPT_MAX_ATTEMPTS or not is_retryable_transcript_structure_error(exc):
                raise
            retry_feedback = build_transcript_structure_retry_feedback(exc)
    else:
        raise last_error or RuntimeError("Qwen transcript structuring failed")
    attach_transcript_generation_metadata(tree=tree, job=job)
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


def is_retryable_transcript_structure_error(exc: RuntimeError) -> bool:
    message = str(exc)
    return "section.query_graph" in message


def build_transcript_structure_retry_feedback(exc: RuntimeError) -> str:
    return (
        "后端校验错误："
        f"{exc}\n\n"
        "请重新生成完整 transcript JSON。重点修复 section.query_graph：每条 edge 的 from/to "
        "都必须精确引用 nodes 中存在的 id，并且 query_graph 要与 business_frame_text 的单张核心关键帧画面一致。"
    )


def generate_semantic_tree(
    *,
    job: VideoJob,
    source_segments: list[dict[str, Any]],
    duration_ms: int,
    retry_feedback: str = "",
) -> dict[str, Any]:
    if not source_segments:
        raise RuntimeError("Local ASR did not return sentence-level timestamps")
    return QwenTranscriptTreeGenerator.from_settings().generate_tree(
        video_title=job.title or job.filename or job.id,
        filename=job.filename,
        duration=format_duration(duration_ms),
        source_segments=source_segments,
        retry_feedback=retry_feedback,
    )


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
        retry_feedback: str = "",
    ) -> dict[str, Any]:
        prompt = build_transcript_structure_prompt(
            video_title=video_title,
            filename=filename,
            duration=duration,
            source_segments=source_segments,
            max_input_chars=self.max_input_chars,
            retry_feedback=retry_feedback,
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
                "validation_retry": bool(retry_feedback.strip()),
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


def build_transcript_structure_prompt(
    *,
    video_title: str,
    filename: str,
    duration: str,
    source_segments: list[dict[str, Any]],
    max_input_chars: int,
    retry_feedback: str = "",
) -> str:
    source_lines: list[str] = []
    for segment in source_segments:
        index = int(segment.get("index") or len(source_lines))
        start_time = format_timestamp(float(segment.get("start_seconds") or 0.0))
        end_time = format_timestamp(float(segment.get("end_seconds") or segment.get("start_seconds") or 0.0))
        text = str(segment.get("text") or "").strip()
        source_lines.append(f"[{index}] {start_time}-{end_time} {text}")

    source_context = "\n".join(source_lines) if source_lines else "(empty)"
    retry_context = ""
    if retry_feedback.strip():
        retry_context = f"""

上一次输出没有通过后端结构校验，请根据下面的错误重新生成完整 JSON，不要只返回修复片段：
{clip_text(retry_feedback.strip(), max_input_chars // 4)}

重试修正要求：
- 所有 query_graph.edges 里的 from/to 必须引用同一 query_graph.nodes 中已经存在的 node.id。
- 如果一条关系对应的节点没有出现在 nodes 中，必须先补齐节点，或者删除这条关系。
- business_frame_text 仍然必须保持单张关键帧 query，不要因为重试又写成完整动作流程。
"""
    return f"""请把下面的 ASR 句子整理成结构化转写 JSON。

硬性要求：
1. 只能基于 source_sentences 里的原始句子重组，不要改写事实含义，不要补充 ASR 没说过的信息。
2. section.source_segment_indices 必须是连续区间，不能跳号，不能拆散后再拼。
3. 如果一个段落需要引用多个 ASR 句子，这些句子必须是原始序列里相邻的连续句子，比如 [3]、[3,4]、[3,4,5]；不允许 [3,5]、[2,4,5]、[6,8] 这种非连续索引。
4. 段落粒度必须是“完整业务步骤”，不要按每颗螺丝、每个孔位、每次拿取、每次对齐这样的微动作拆段。
5. 机械臂装配视频的典型段落粒度是：机械臂组件介绍、组装底座与大臂、组装大臂与小臂、组装手腕关节与小臂、组装夹爪、接线或测试等大的操作步骤。
6. 同一个完整步骤里的连续说明、多个螺丝固定、对齐提醒、完成检查，应尽量合并在同一段里；只有操作对象或装配阶段明显切换时才拆新段。
7. 不要输出 text、source_text 或任何原文字段；text 正文由系统根据 source_segment_indices 从 ASR 原句拼接生成。
8. 每个段落必须输出 polished_text，作为给人阅读的润色后正文。polished_text 要保留原文事实和顺序，把口语表达、重复语气词、断句不顺、口语数字润色成清晰自然的教程正文，不要新增 ASR 没说过的信息。
9. 每个段落必须输出 business_frame_text，作为待匹配文本，专门用于图索引和图文匹配。
10. business_frame_text 必须是适合匹配单张关键帧的核心画面描述，而不是本段完整操作流程的复述。
11. business_frame_text 优先描述一张图里稳定可见的对象、对象关系和完成/安装状态，例如“大臂与底座连接”“舵机靠近底座金属固定件”“线缆已插入端子”。
12. business_frame_text 不要枚举连续动作、先后顺序或每颗螺丝的安装过程；不要写“依次拧紧四个螺丝”“活动一下检查”等跨时间动作，只保留最能代表该业务步骤的一张关键帧画面。
13. business_frame_text 不要写教程语气、原因、目的、注意事项、风险解释或抽象总结；只写画面中可见对象、关系和状态，长度控制在 30-100 个中文字符。
14. 每个段落必须直接输出 section.query_graph；不要输出 visual_operations、key_operations、frame_query、frame_queries、visual_terms、operation_text。
15. query_graph 是后续图索引和图文匹配的唯一结构化输入，必须围绕 business_frame_text 这张核心关键帧画面生成，不要覆盖本段所有微动作。
16. query_graph 至少包含 nodes 和 edges，不要留空 query_graph。
17. nodes 中每个节点必须包含 id、label、role、required、weight。
18. edges 中每条边必须包含 from、relation、to、required、weight。
19. required 用于表示这个节点或关系是否是该段业务步骤核心关键帧的必要证据。
20. weight 用 0 到 1 的小数表示相对重要性，整组节点和边的权重不必强制和为 1，但请合理分配。
21. 不要输出 query_graph.state；完成状态、安装状态、连接状态应通过 business_frame_text 以及 nodes / edges 表达。
22. 只有当手部或工具动作是判断该业务步骤的必要证据时，才把手部或工具放进 query_graph；主体必须仍然是设备、部件、孔位、连接位置或完成状态。

视频名称：{video_title}
文件名：{filename}
视频时长：{duration}

source_sentences:
{clip_text(source_context, max_input_chars // 2)}
{retry_context}

输出 JSON：
{{
  "title": "{video_title}",
  "sections": [
    {{
      "index": 1,
      "title": "段落标题",
      "source_segment_indices": [0, 1],
      "polished_text": "润色后的本段教程正文",
      "business_frame_text": "画面中舵机靠近底座金属固定件，大臂与底座处于连接安装位置",
      "query_graph": {{
        "nodes": [
          {{"id": "servo", "label": "servo", "role": "part", "required": true, "weight": 0.5}},
          {{"id": "base", "label": "base", "role": "part", "required": true, "weight": 0.5}}
        ],
        "edges": [
          {{"from": "servo", "relation": "near", "to": "base", "required": false, "weight": 1.0}}
        ]
      }}
    }}
  ]
}}
"""


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
        model_text = str(raw_section.get("text") or "").strip()
        source_indices = normalize_source_indices(raw_section.get("source_segment_indices") or raw_section.get("source_chunks"))
        source_indices = sorted(source_indices)
        section_context = (
            f"section_position={index}, raw_section_index={raw_section.get('index')!r}, "
            f"section_title={section_title!r}, source_segment_indices={source_indices}, text={model_text[:160]!r}"
        )
        if not source_indices:
            raise RuntimeError(f"Qwen transcript structuring must return source_segment_indices: {section_context}")
        if not source_indices_are_contiguous(source_indices):
            raise RuntimeError(
                "Qwen transcript structuring must return contiguous source_segment_indices: "
                f"{section_context}; expected a single continuous interval such as [3], [3,4], [3,4,5]"
            )
        if any(source_index in assigned_source_indices for source_index in source_indices):
            raise RuntimeError(
                "Qwen transcript structuring reused an ASR sentence index: "
                f"{section_context}; already_assigned_indices={sorted(assigned_source_indices)}"
            )
        start, end = section_range_from_source_segments(source_segments, source_indices)
        if start is None or end is None:
            raise RuntimeError(f"ASR sentence timestamps are missing for one or more transcript sections: {section_context}")
        assigned_source_indices.update(source_indices)
        start = clamp_seconds(start, duration_seconds)
        end = clamp_seconds(max(end, start), duration_seconds)
        asr_text = asr_text_from_segments(source_segments, source_indices)
        polished_text = re.sub(
            r"\s+",
            " ",
            str(raw_section.get("polished_text") or "").strip(),
        ).strip()
        if not polished_text:
            raise RuntimeError(f"Qwen transcript structuring must return polished_text: {section_context}")
        business_frame_text = re.sub(
            r"\s+",
            " ",
            str(raw_section.get("business_frame_text") or "").strip(),
        ).strip()
        if not business_frame_text:
            raise RuntimeError(f"Qwen transcript structuring must return business_frame_text: {section_context}")
        query_graph = normalize_section_query_graph(
            raw_section,
            section_index=index,
            section_title=section_title,
            source_indices=source_indices,
            business_frame_text=business_frame_text,
        )
        sections.append(
            {
                "index": len(sections) + 1,
                "title": section_title[:80],
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "start_time": format_timestamp(start),
                "end_time": format_timestamp(end),
                "text": asr_text,
                "polished_text": polished_text,
                "business_frame_text": business_frame_text,
                "query_graph": query_graph,
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
        text = str(section.get("text") or "").strip()
        polished_text = str(section.get("polished_text") or "").strip()
        business_frame_text = transcript_section_business_frame_text(section)
        lines.extend(
            [
                f"## {section.get('index')}. {section.get('title')}",
                "",
                f"**时间范围：** {section.get('start_time')} - {section.get('end_time')}",
                "",
                "### 正文",
                "",
                text or "未识别。",
                "",
                "### 润色后正文",
                "",
                polished_text or "未识别。",
                "",
                "### 待匹配文本",
                "",
                business_frame_text or "未识别。",
                "",
            ]
        )
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def transcript_section_business_frame_text(section: dict[str, Any]) -> str:
    return str(section.get("business_frame_text") or "").strip()


def normalize_section_query_graph(
    raw_section: dict[str, Any],
    *,
    section_index: int,
    section_title: str,
    source_indices: list[int],
    business_frame_text: str,
) -> dict[str, Any]:
    raw_graph = raw_section.get("query_graph")
    context = (
        f"section_index={section_index}, section_title={section_title!r}, "
        f"source_segment_indices={source_indices}, business_frame_text={business_frame_text[:160]!r}"
    )
    try:
        return normalize_query_graph(raw_graph)
    except QueryGraphError as exc:
        raise RuntimeError(f"{exc}: {context}") from exc


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


def attach_semantic_frame_report_to_transcript_tree(*, tree: dict[str, Any], semantic_report: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(semantic_report, dict):
        return tree
    body = tree.setdefault("tree", {})
    sections = body.get("sections")
    report_sections = semantic_report.get("sections")
    if not isinstance(sections, list) or not isinstance(report_sections, list):
        return tree

    report_by_index: dict[int, dict[str, Any]] = {}
    for position, report_section in enumerate(report_sections, start=1):
        if not isinstance(report_section, dict):
            continue
        try:
            section_index = int(report_section.get("index") or position)
        except (TypeError, ValueError):
            continue
        report_by_index[section_index] = report_section

    for section in sections:
        if not isinstance(section, dict):
            continue
        try:
            section_index = int(section.get("index") or 0)
        except (TypeError, ValueError):
            continue
        report_section = report_by_index.get(section_index)
        if not isinstance(report_section, dict):
            continue
        business_frame = business_frame_from_semantic_section(report_section)
        raw_keyframe = raw_keyframe_from_semantic_section(report_section, business_frame=business_frame)
        if raw_keyframe:
            section["raw_keyframe"] = raw_keyframe
        if business_frame:
            section["business_frame"] = business_frame
    return tree


def build_markdown_from_transcript_tree(
    *,
    tree: dict[str, Any],
    frame_url: Callable[[dict[str, Any]], str] | None = None,
    embed_frames: bool = False,
) -> str:
    """渲染视频分析 Markdown。每个段落一张分数最高的业务帧。

    frame_url:    把业务帧映射成文档里的链接地址,默认用帧自带的 API url。
                  自包含导出时传入包内相对路径的解析函数。
    embed_frames: True 时用 ![]() 内联图片,False 时用 []() 普通链接。
    """
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
        "---",
        "",
        f"# {title}",
        "",
    ]
    for section in body.get("sections") or []:
        text = str(section.get("text") or "").strip()
        polished_text = str(section.get("polished_text") or "").strip()
        business_frame = business_frame_for_markdown(section)
        lines.extend(
            [
                f"## {section.get('index')}. {section.get('title')}",
                "",
                f"**时间范围：** {section.get('start_time')} - {section.get('end_time')}",
                "",
                "### 润色后正文",
                "",
                polished_text or text or "未识别。",
                "",
                "### 业务帧",
                "",
            ]
        )
        append_frame_link(
            lines,
            business_frame,
            label="业务帧",
            include_score=True,
            frame_url=frame_url,
            embed=embed_frames,
        )
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def raw_keyframe_from_semantic_section(
    section: dict[str, Any] | None,
    *,
    business_frame: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(section, dict):
        return None
    raw_keyframes = unique_slim_frames(section.get("raw_frames") or [])
    if not raw_keyframes:
        return None
    if business_frame:
        matched = matching_frame(raw_keyframes, business_frame)
        if matched:
            return matched
    return raw_keyframes[0]


def business_frame_from_semantic_section(section: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(section, dict):
        return None
    candidates: list[dict[str, Any]] = []
    for match in section.get("query_graph_matches") or []:
        if not isinstance(match, dict):
            continue
        candidates.extend(frame for frame in match.get("frames") or [] if isinstance(frame, dict))
    if not candidates:
        candidates.extend(frame for frame in section.get("semantic_frames") or [] if isinstance(frame, dict))
    if not candidates:
        candidates.extend(frame for frame in section.get("candidate_business_frames") or [] if isinstance(frame, dict))
    return best_scored_frame(unique_slim_frames(candidates))


def raw_keyframe_for_markdown(section: dict[str, Any]) -> dict[str, Any] | None:
    raw_keyframe = section.get("raw_keyframe")
    if isinstance(raw_keyframe, dict):
        return slim_keyframe(raw_keyframe)
    raw_keyframes = unique_slim_frames(section.get("raw_keyframes") or section.get("frames") or [])
    business_frame = business_frame_for_markdown(section)
    if business_frame:
        matched = matching_frame(raw_keyframes, business_frame)
        if matched:
            return matched
    return raw_keyframes[0] if raw_keyframes else None


def business_frame_for_markdown(section: dict[str, Any]) -> dict[str, Any] | None:
    business_frame = section.get("business_frame")
    if isinstance(business_frame, dict):
        return slim_keyframe(business_frame)
    business_frames = unique_slim_frames(section.get("business_frames") or section.get("semantic_frames") or [])
    return best_scored_frame(business_frames)


def best_scored_frame(frames: list[dict[str, Any]]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score: float | None = None
    for frame in frames:
        score = numeric_score(frame.get("score"))
        if score is None:
            continue
        if best is None or best_score is None or score > best_score:
            best = frame
            best_score = score
    return best or (frames[0] if frames else None)


def matching_frame(frames: list[dict[str, Any]], target: dict[str, Any]) -> dict[str, Any] | None:
    target_id = target.get("id")
    target_object_key = target.get("object_key")
    target_url = target.get("url")
    for frame in frames:
        if target_id is not None and frame.get("id") is not None and str(frame.get("id")) == str(target_id):
            return frame
        if target_object_key and frame.get("object_key") == target_object_key:
            return frame
        if target_url and frame.get("url") == target_url:
            return frame
    return None


def unique_slim_frames(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        slim = slim_keyframe(candidate)
        if slim is None:
            continue
        key = str(slim.get("id") or slim.get("object_key") or slim.get("url") or "")
        if key in seen:
            continue
        seen.add(key)
        result.append(slim)
    return result


def append_frame_link(
    lines: list[str],
    frame: dict[str, Any] | None,
    *,
    label: str,
    include_score: bool,
    frame_url: Callable[[dict[str, Any]], str] | None = None,
    embed: bool = False,
) -> None:
    if not frame:
        lines.append("- 无")
        return
    time_text = frame.get("timestamp_time") or "未知时间"
    caption = frame.get("caption") or label
    url = frame_url(frame) if frame_url is not None else frame.get("url")
    if url:
        prefix = "!" if embed else ""
        lines.append(f"- **{time_text}** {prefix}[{caption}]({url})")
    else:
        lines.append(f"- **{time_text}** {caption}")
    score = numeric_score(frame.get("score"))
    if include_score and score is not None:
        lines.append(f"  - 相似度分数：{score:.4f}")
    # 不输出 object_key,避免暴露存储实现细节


def slim_keyframe(frame: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(frame, dict):
        return None
    result = {
        "id": frame.get("id") or frame.get("frame_id"),
        "timestamp_seconds": frame.get("timestamp_seconds"),
        "timestamp_time": frame.get("timestamp_time"),
        "caption": frame.get("caption") or "关键帧",
        "object_key": frame.get("object_key"),
        "url": frame.get("url"),
        "score": frame.get("score"),
        "rank": frame.get("rank"),
    }
    if not any(result.get(key) for key in ("id", "object_key", "url")):
        return None
    return result


def numeric_score(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def asr_text_from_segments(source_segments: list[dict[str, Any]], indices: list[int]) -> str:
    if not source_segments or not indices:
        return ""
    by_index = {int(segment.get("index") or 0): segment for segment in source_segments}
    parts = [
        re.sub(r"\s+", " ", str(by_index[index].get("text") or "").strip())
        for index in indices
        if index in by_index and str(by_index[index].get("text") or "").strip()
    ]
    return " ".join(parts).strip()


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
    except json.JSONDecodeError as first_exc:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise RuntimeError(
                f"Transcript structure response is not valid JSON: {first_exc.msg} at line {first_exc.lineno}, "
                f"column {first_exc.colno}; head={text[:240]!r}"
            ) from first_exc
        json_text = match.group(0)
        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError as second_exc:
            line_no = second_exc.lineno
            col_no = second_exc.colno
            lines = json_text.splitlines()
            bad_line = lines[line_no - 1] if 1 <= line_no <= len(lines) else ""
            start = max(0, col_no - 80)
            end = min(len(bad_line), col_no + 80)
            excerpt = bad_line[start:end] if bad_line else json_text[:240]
            raise RuntimeError(
                f"Transcript structure JSON is malformed: {second_exc.msg} at line {line_no}, column {col_no}; "
                f"excerpt={excerpt!r}"
            ) from second_exc
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
