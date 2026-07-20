from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.services.audit import finish_call, logged_call_with_session


@dataclass(frozen=True, slots=True)
class LocalAsrResult:
    text: str
    language: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)


class LocalAsrError(RuntimeError):
    pass


class LocalAsrClient:
    def __init__(self, api_base_url: str | None = None) -> None:
        self.api_base_url = (api_base_url or settings.local_asr_api_base_url).rstrip("/")

    def enabled(self) -> bool:
        return bool(self.api_base_url)

    def transcribe_video_file(self, *, path: Path, media_type: str, language: str | None = None) -> LocalAsrResult:
        if not self.enabled():
            raise LocalAsrError("LOCAL_ASR_API_BASE_URL is not configured")
        if not path.exists() or path.stat().st_size <= 0:
            raise LocalAsrError("Local ASR input video is missing or empty")

        with logged_call_with_session(
            interface_type="model",
            tool_or_endpoint="local_asr.audio_transcriptions",
            request={
                "model": settings.local_asr_model_label,
                "endpoint": f"{self.api_base_url}/v1/audio/transcriptions",
                "filename": path.name,
                "media_type": media_type,
                "size_bytes": path.stat().st_size,
                "language": language or settings.local_asr_language,
            },
        ) as (audit_session, call_id):
            payload = self._post_with_retries(path=path, media_type=media_type, language=language)
            text = extract_text(payload)
            if not text:
                raise LocalAsrError("Local ASR response did not include transcript text")
            raw_language = payload.get("language")
            result_language = str(raw_language).strip() if raw_language else language
            finish_call(
                audit_session,
                call_id,
                {
                    "model": settings.local_asr_model_label,
                    "usage": payload.get("usage", {}),
                    "text_chars": len(text),
                    "language": result_language,
                },
            )
            return LocalAsrResult(text=text, language=result_language, raw_response=payload)

    def _post_with_retries(self, *, path: Path, media_type: str, language: str | None) -> dict[str, Any]:
        timeout = httpx.Timeout(settings.local_asr_timeout_seconds, connect=20.0)
        data = {
            "language": language or settings.local_asr_language,
            "response_format": "verbose_json",
            "timestamp_granularities[]": "segment",
        }
        last_error = ""
        for attempt in range(max(1, settings.local_asr_max_retries)):
            try:
                with path.open("rb") as file_obj:
                    files = {"file": (path.name, file_obj, media_type or "video/mp4")}
                    with httpx.Client(timeout=timeout, trust_env=False) as client:
                        response = client.post(f"{self.api_base_url}/v1/audio/transcriptions", files=files, data=data)
                if response.status_code < 400:
                    parsed = response.json()
                    if isinstance(parsed, dict):
                        return parsed
                    raise LocalAsrError("Local ASR returned non-object JSON")
                last_error = f"HTTP {response.status_code}: {response.text[:1000]}"
            except (httpx.HTTPError, OSError, ValueError) as exc:
                last_error = f"{exc.__class__.__name__}: {exc}"
            if attempt + 1 < settings.local_asr_max_retries:
                time.sleep(min(2**attempt, 10))
        raise LocalAsrError(f"Local ASR call failed: {last_error}")


def extract_text(payload: dict[str, Any]) -> str:
    text = payload.get("text") or payload.get("transcription")
    if text:
        return str(text).strip()

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else None
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()
    return ""


local_asr_client = LocalAsrClient()
