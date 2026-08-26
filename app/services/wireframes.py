from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.video_config import video_settings as settings
from app.models.video import VideoFrame
from app.services.audit import finish_call, logged_call_with_session
from app.services.storage import StorageService, storage_service
from app.services.video_outputs import wireframe_object_key_for_frame


class WireframeService:
    def __init__(self, storage: StorageService = storage_service) -> None:
        self.storage = storage

    def generate_wireframe(self, session: Session, frame_id: int) -> dict:
        frame = session.get(VideoFrame, frame_id)
        if frame is None:
            raise ValueError(f"Frame not found: {frame_id}")
        source_bytes = self.storage.get_bytes(bucket=frame.bucket, object_key=frame.object_key)
        image_bytes, media_type, tool_result = self._run_qwen_wireframe(frame=frame, source_bytes=source_bytes)
        object_key = wireframe_object_key_for_frame(frame)
        stored = self.storage.upload_bytes(object_key=object_key, content=image_bytes, media_type=media_type)
        return {
            "frame_id": frame_id,
            "video_id": frame.video_id,
            "bucket": stored.bucket,
            "object_key": stored.object_key,
            "url": f"/api/objects/{stored.object_key}",
            "media_type": stored.media_type,
            "tool_result": tool_result,
        }

    def _run_qwen_wireframe(self, *, frame: VideoFrame, source_bytes: bytes) -> tuple[bytes, str, dict]:
        return self._run_qwen_wireframe_bytes(input_name=f"frame-{frame.id}.jpg", source_bytes=source_bytes)

    def generate_uploaded_image_wireframe(self, *, filename: str, content: bytes, media_type: str) -> dict:
        image_id = uuid.uuid4().hex
        safe_name = safe_filename(filename)
        source_key = f"wireframes/images/{image_id}/source/{safe_name}"
        source = self.storage.upload_bytes(object_key=source_key, content=content, media_type=media_type)
        image_bytes, output_media_type, tool_result = self._run_qwen_wireframe_bytes(input_name=safe_name, source_bytes=content)
        output_key = f"wireframes/images/{image_id}/result.png"
        result = self.storage.upload_bytes(object_key=output_key, content=image_bytes, media_type=output_media_type)
        return {
            "image_id": image_id,
            "source": {
                "bucket": source.bucket,
                "object_key": source.object_key,
                "url": f"/api/objects/{source.object_key}",
                "media_type": source.media_type,
            },
            "wireframe": {
                "bucket": result.bucket,
                "object_key": result.object_key,
                "url": f"/api/objects/{result.object_key}",
                "media_type": result.media_type,
            },
            "tool_result": tool_result,
        }

    def _run_qwen_wireframe_bytes(self, *, input_name: str, source_bytes: bytes) -> tuple[bytes, str, dict]:
        tool_dir = Path(settings.wireframe_tool_dir)
        if not tool_dir.is_absolute():
            tool_dir = Path.cwd() / tool_dir
        script = tool_dir / "scripts" / "generate_wireframe.py"
        prompt = tool_dir / "prompts" / "wireframe-prompt.md"
        if not script.exists():
            raise FileNotFoundError(f"Wireframe tool script not found: {script}")
        if not prompt.exists():
            raise FileNotFoundError(f"Wireframe prompt not found: {prompt}")

        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / input_name
            output_path = Path(tmp) / f"{Path(input_name).stem}-wireframe.png"
            input_path.write_bytes(source_bytes)
            command = [
                sys.executable,
                str(script),
                "--input",
                str(input_path),
                "--output",
                str(output_path),
                "--prompt",
                str(prompt),
                "--image-model",
                settings.wireframe_image_model,
                "--size",
                settings.wireframe_image_size,
                "--timeout",
                str(settings.wireframe_timeout_seconds),
                "--overwrite",
            ]
            if settings.model_dashscope_base_url:
                command.extend(["--base-url", settings.model_dashscope_base_url])
            env = os.environ.copy()
            if settings.model_api_key:
                env.setdefault("MODEL_API_KEY", settings.model_api_key)
                env.setdefault("DASHSCOPE_API_KEY", settings.model_api_key)
            if settings.model_dashscope_base_url:
                env.setdefault("MODEL_DASHSCOPE_BASE_URL", settings.model_dashscope_base_url)
            with logged_call_with_session(
                interface_type="model",
                tool_or_endpoint="qwen.image.wireframe",
                request={
                    "model": settings.wireframe_image_model,
                    "input_name": input_name,
                    "input_bytes": len(source_bytes),
                    "size": settings.wireframe_image_size,
                },
            ) as (audit_session, call_id):
                result = subprocess.run(command, check=False, capture_output=True, text=True, env=env, timeout=settings.wireframe_timeout_seconds + 30)
                try:
                    payload = json.loads(result.stdout.strip() or "{}")
                except json.JSONDecodeError:
                    payload = {"ok": False, "stdout": result.stdout[-1000:], "stderr": result.stderr[-1000:]}
                if result.returncode != 0 or not output_path.exists():
                    raise RuntimeError(f"Qwen wireframe generation failed: {payload}")
                image_bytes = output_path.read_bytes()
                finish_call(
                    audit_session,
                    call_id,
                    {
                        "model": settings.wireframe_image_model,
                        "usage": payload.get("usage", {}),
                        "output_bytes": len(image_bytes),
                        "tool_payload": payload,
                    },
                )
                return image_bytes, "image/png", payload


def safe_filename(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value) or "image.png"


wireframe_service = WireframeService()
