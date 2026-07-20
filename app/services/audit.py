from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import CallLog


def default_caller() -> str:
    return settings.mcp_client_id or settings.mcp_user or settings.mcp_api_key or "anonymous"


def summarize(value: object, *, max_chars: int = 1000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


@contextmanager
def logged_call(
    session: Session,
    *,
    interface_type: str,
    tool_or_endpoint: str,
    caller: str | None = None,
    request: object | None = None,
    video_id: str | None = None,
    standard_id: str | None = None,
) -> Iterator[str]:
    call_id = uuid.uuid4().hex
    started = time.perf_counter()
    log = CallLog(
        id=call_id,
        interface_type=interface_type,
        caller=caller or default_caller(),
        tool_or_endpoint=tool_or_endpoint,
        request_summary=summarize(request or {}),
        status="running",
        video_id=video_id,
        standard_id=standard_id,
    )
    session.add(log)
    session.commit()
    try:
        yield call_id
    except Exception as exc:
        log.status = "failed"
        log.error_message = str(exc)
        log.duration_ms = int((time.perf_counter() - started) * 1000)
        session.add(log)
        session.commit()
        raise
    else:
        log.status = "success"
        log.duration_ms = int((time.perf_counter() - started) * 1000)
        session.add(log)
        session.commit()


@contextmanager
def logged_call_with_session(
    *,
    interface_type: str,
    tool_or_endpoint: str,
    caller: str | None = None,
    request: object | None = None,
    video_id: str | None = None,
    standard_id: str | None = None,
) -> Iterator[tuple[Session, str]]:
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        with logged_call(
            session,
            interface_type=interface_type,
            tool_or_endpoint=tool_or_endpoint,
            caller=caller,
            request=request,
            video_id=video_id,
            standard_id=standard_id,
        ) as call_id:
            yield session, call_id


def finish_call(session: Session, call_id: str, response: object) -> None:
    log = session.get(CallLog, call_id)
    if log is None:
        return
    log.response_summary = summarize(response)
    session.add(log)
    session.commit()
