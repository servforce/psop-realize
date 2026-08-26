from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Iterator


_current_video_id: ContextVar[str | None] = ContextVar("current_video_usage_video_id", default=None)
_current_parent_call_id: ContextVar[str | None] = ContextVar("current_video_usage_parent_call_id", default=None)


def summarize(value: object, *, max_chars: int = 1000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@contextmanager
def logged_call(
    session: object,
    *,
    interface_type: str,
    tool_or_endpoint: str,
    caller: str | None = None,
    request: object | None = None,
    video_id: str | None = None,
) -> Iterator[str]:
    call_id = uuid.uuid4().hex
    video_context_token = _current_video_id.set(video_id) if video_id else None
    record = _create_usage_record(
        session=session,
        call_id=call_id,
        interface_type=interface_type,
        tool_or_endpoint=tool_or_endpoint,
        caller=caller,
        request=request,
        video_id=video_id,
    )
    parent_context_token = (
        _current_parent_call_id.set(call_id)
        if record and interface_type == "background" and tool_or_endpoint == "video_parse_job"
        else None
    )
    try:
        yield call_id
    except Exception:
        if record:
            _finish_usage_record(session, call_id, {"error": "call failed"}, status="failed")
        raise
    finally:
        if parent_context_token is not None:
            _current_parent_call_id.reset(parent_context_token)
        if video_context_token is not None:
            _current_video_id.reset(video_context_token)


@contextmanager
def logged_call_with_session(
    *,
    interface_type: str,
    tool_or_endpoint: str,
    caller: str | None = None,
    request: object | None = None,
    video_id: str | None = None,
) -> Iterator[tuple[object, str]]:
    from app.db.video import VideoSessionLocal

    call_id = uuid.uuid4().hex
    audit_session = VideoSessionLocal()
    record = _create_usage_record(
        session=audit_session,
        call_id=call_id,
        interface_type=interface_type,
        tool_or_endpoint=tool_or_endpoint,
        caller=caller,
        request=request,
        video_id=video_id,
    )
    try:
        yield audit_session, call_id
    except Exception:
        if record:
            _finish_usage_record(audit_session, call_id, {"error": "call failed"}, status="failed")
        raise
    finally:
        audit_session.close()


def finish_call(session: object, call_id: str, response: object) -> None:
    _finish_usage_record(session, call_id, response, status="completed")


def _create_usage_record(
    *,
    session: object,
    call_id: str,
    interface_type: str,
    tool_or_endpoint: str,
    caller: str | None,
    request: object | None,
    video_id: str | None,
) -> bool:
    del caller
    feature_name = feature_name_for_call(
        interface_type=interface_type,
        tool_or_endpoint=tool_or_endpoint,
        request=request,
    )
    if not feature_name:
        return False
    try:
        from app.models.video import VideoUsageRecord

        payload = dict(request) if isinstance(request, dict) else {}
        parent_call_id = _current_parent_call_id.get()
        if parent_call_id and interface_type == "model":
            payload["parent_call_id"] = parent_call_id
        record_video_id = video_id or _current_video_id.get()
        record = VideoUsageRecord(
            id=call_id,
            video_id=record_video_id,
            feature_name=feature_name,
            interface_type=interface_type,
            tool_or_endpoint=tool_or_endpoint,
            model=model_for_call(tool_or_endpoint=tool_or_endpoint, request=payload),
            status="processing",
            request_json=summarize(payload, max_chars=4000),
            response_json="{}",
            created_at=utcnow(),
        )
        session.add(record)
        session.commit()
        return True
    except Exception:
        _rollback_quietly(session)
        return False


def _finish_usage_record(session: object, call_id: str, response: object, *, status: str) -> None:
    try:
        from app.models.video import VideoUsageRecord

        record = session.get(VideoUsageRecord, call_id)
        if record is None:
            return
        usage = extract_usage(response)
        if usage["total_tokens"] <= 0 and is_feature_parent_record(record):
            usage = aggregate_child_usage(session, record)
        record.prompt_tokens = usage["prompt_tokens"]
        record.completion_tokens = usage["completion_tokens"]
        record.total_tokens = usage["total_tokens"]
        if not record.model:
            record.model = model_for_call(tool_or_endpoint=record.tool_or_endpoint, request={}, response=response)
        record.status = status
        record.response_json = summarize(response or {}, max_chars=4000)
        record.completed_at = utcnow()
        session.add(record)
        session.commit()
    except Exception:
        _rollback_quietly(session)


def feature_name_for_call(*, interface_type: str, tool_or_endpoint: str, request: object | None) -> str:
    if tool_or_endpoint in {"local_asr.audio_transcriptions", "qwen.chat.completions.transcript_tree"}:
        return "转写文本"
    if tool_or_endpoint == "qwen.chat.completions.frame_selection":
        return "筛选业务帧"
    if tool_or_endpoint == "qwen.image.wireframe":
        return "线框图"
    if interface_type == "background" and tool_or_endpoint == "video_parse_job":
        mode = (request or {}).get("mode") if isinstance(request, dict) else None
        if mode == "transcript":
            return "转写文本"
        if mode == "keyframes":
            return "筛选业务帧"
        if mode == "markdown":
            return "生成 Markdown"
    return ""


def model_for_call(*, tool_or_endpoint: str, request: dict, response: object | None = None) -> str:
    response_model = response.get("model") if isinstance(response, dict) else ""
    request_model = request.get("model") if isinstance(request, dict) else ""
    if response_model:
        return str(response_model)
    if request_model:
        return str(request_model)
    if tool_or_endpoint == "video_parse_job":
        mode = request.get("mode") if isinstance(request, dict) else None
        if mode == "transcript":
            try:
                from app.core.video_config import video_settings as settings

                return f"{settings.local_asr_model_label} + {settings.transcript_structure_model}"
            except Exception:
                return "本地 ASR + 结构化转写模型"
        if mode == "keyframes":
            return "本地 yolov8s-world + sam2.1_b.pt"
        if mode == "markdown":
            return "本地 Markdown 生成"
    return "本地流程"


def extract_usage(response: object) -> dict[str, int]:
    usage = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    prompt_tokens = int_value(
        usage.get("prompt_tokens"),
        usage.get("input_tokens"),
        usage.get("promptTokens"),
        usage.get("inputTokens"),
    )
    completion_tokens = int_value(
        usage.get("completion_tokens"),
        usage.get("output_tokens"),
        usage.get("completionTokens"),
        usage.get("outputTokens"),
    )
    total_tokens = int_value(
        usage.get("total_tokens"),
        usage.get("totalTokens"),
        prompt_tokens + completion_tokens,
    )
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def int_value(*values: object) -> int:
    for value in values:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0


def _rollback_quietly(session: object) -> None:
    try:
        session.rollback()
    except Exception:
        pass


def is_feature_parent_record(record: object) -> bool:
    return (
        getattr(record, "interface_type", "") == "background"
        and getattr(record, "tool_or_endpoint", "") == "video_parse_job"
    )


def aggregate_child_usage(session: object, parent: object) -> dict[str, int]:
    try:
        from sqlalchemy import select

        from app.models.video import VideoUsageRecord

        rows = list(
            session.scalars(
                select(VideoUsageRecord).where(
                    VideoUsageRecord.id != parent.id,
                    VideoUsageRecord.video_id == parent.video_id,
                    VideoUsageRecord.feature_name == parent.feature_name,
                    VideoUsageRecord.interface_type == "model",
                    VideoUsageRecord.created_at >= parent.created_at,
                    VideoUsageRecord.request_json.contains(parent.id),
                )
            )
        )
    except Exception:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    prompt_tokens = sum(int(row.prompt_tokens or 0) for row in rows)
    completion_tokens = sum(int(row.completion_tokens or 0) for row in rows)
    total_tokens = sum(int(row.total_tokens or 0) for row in rows)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
