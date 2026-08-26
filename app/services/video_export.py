from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

from app.core.video_config import video_settings as settings
from app.models.video import VideoJob
from app.services.storage import storage_service
from app.services.transcript_tree import (
    build_markdown_from_transcript_tree,
    business_frame_for_markdown,
    numeric_score,
)
from app.services.video_outputs import transcript_tree_object_key

FRAMES_DIR = "frames"
MARKDOWN_NAME = "result.md"
MANIFEST_NAME = "manifest.json"
ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def build_export_package(job: VideoJob) -> bytes:
    """打包自包含 zip:result.md + frames/ + manifest.json。

    每个段落对应一张分数最高的业务帧。Markdown 用包内相对路径引用图片,
    调用方解压即可离线阅读,无需了解底层存储实现。
    """
    tree = load_transcript_tree(job)
    entries = collect_section_frames(tree)

    # 先取字节再渲染:读失败的帧不进 local_names,markdown 就不会留下坏链接
    images: dict[str, bytes] = {}
    for entry in entries:
        local_name = entry["local_name"]
        if not local_name or local_name in images:
            continue
        content = read_frame_bytes(job, entry["object_key"])
        if content:
            images[local_name] = content

    local_names = {
        e["object_key"]: e["local_name"]
        for e in entries
        if e["object_key"] and e["local_name"] in images
    }
    markdown = build_markdown_from_transcript_tree(
        tree=tree,
        frame_url=lambda frame: package_frame_url(frame, local_names),
        embed_frames=True,
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MARKDOWN_NAME, markdown)
        for local_name, content in images.items():
            archive.writestr(f"{FRAMES_DIR}/{local_name}", content)
        archive.writestr(
            MANIFEST_NAME,
            json.dumps(build_manifest(job, entries, set(images)), ensure_ascii=False, indent=2),
        )
    return buffer.getvalue()


def package_frame_url(frame: dict[str, Any], local_names: dict[str, str]) -> str:
    """把业务帧映射成包内相对路径。

    前缀 ./ 是必需的:workbench 的 safeMarkdownUrl 只放行 http(s):、data:image/、
    /、./、../、# 开头的地址,裸的 frames/x.jpg 会被丢弃。
    """
    local_name = local_names.get(frame.get("object_key") or "")
    return f"./{FRAMES_DIR}/{local_name}" if local_name else ""


def build_manifest(job: VideoJob, entries: list[dict], written: set[str]) -> dict[str, Any]:
    """程序化消费的索引:章节、帧、元信息。"""
    return {
        "video_id": job.id,
        "title": job.title,
        "filename": job.filename,
        "duration_ms": job.duration_ms,
        "sections": [
            {
                "index": e["section"].get("index"),
                "title": e["section"].get("title"),
                "start_time": e["section"].get("start_time"),
                "end_time": e["section"].get("end_time"),
                "business_frame": {
                    "timestamp": e["business_frame"].get("timestamp_time"),
                    "caption": e["business_frame"].get("caption"),
                    "score": numeric_score(e["business_frame"].get("score")),
                    "path": f"{FRAMES_DIR}/{e['local_name']}" if e["local_name"] in written else None,
                }
                if e["business_frame"]
                else None,
            }
            for e in entries
        ],
    }


def load_transcript_tree(job: VideoJob) -> dict[str, Any]:
    try:
        return json.loads(
            storage_service.get_bytes(
                bucket=job.source_bucket,
                object_key=transcript_tree_object_key(job.id),
            ).decode("utf-8", errors="replace")
        )
    except Exception:
        return {}


def collect_section_frames(tree: dict[str, Any]) -> list[dict[str, Any]]:
    """为每个段落取分数最高的业务帧,分配包内文件名。"""
    sections = (tree.get("tree") or {}).get("sections") or []
    entries: list[dict[str, Any]] = []
    used: dict[str, str] = {}
    for idx, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        business_frame = business_frame_for_markdown(section)
        object_key = (business_frame or {}).get("object_key") or ""
        entries.append(
            {
                "section": section,
                "business_frame": business_frame,
                "object_key": object_key,
                "local_name": assign_local_name(object_key, idx, used) if object_key else "",
            }
        )
    return entries


def assign_local_name(object_key: str, idx: int, used: dict[str, str]) -> str:
    """同一 object_key 复用同名文件;不同 key 撞名时加后缀区分。"""
    if object_key in used:
        return used[object_key]
    candidate = safe_frame_filename(object_key, fallback_index=idx)
    if candidate in used.values():
        stem, suffix = Path(candidate).stem, Path(candidate).suffix
        candidate = f"{stem}_{idx:03d}{suffix}"
    used[object_key] = candidate
    return candidate


def safe_frame_filename(object_key: str, fallback_index: int) -> str:
    """从 object_key 提取安全文件名,阻断路径穿越,保留合法后缀。"""
    raw_name = object_key.replace("\\", "/").rsplit("/", 1)[-1].strip()
    suffix = Path(raw_name).suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        suffix = ".jpg"
    stem = re.sub(r"[^A-Za-z0-9._\-]+", "_", Path(raw_name).stem).strip("._-")
    return f"{stem or f'frame_{fallback_index:05d}'}{suffix}"


def read_frame_bytes(job: VideoJob, object_key: str) -> bytes | None:
    """按 job 所属 bucket 优先读取,回落到默认 bucket。"""
    if not object_key:
        return None
    buckets = [b for b in (job.source_bucket, settings.object_store_bucket) if b]
    for bucket in dict.fromkeys(buckets):  # 去重,两者相同时不重复请求
        try:
            return storage_service.get_bytes(bucket=bucket, object_key=object_key)
        except Exception:
            continue
    return None
