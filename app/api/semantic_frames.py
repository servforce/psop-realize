from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.core.video_config import video_settings as settings
from app.db.video import VideoSessionLocal
from app.services.audit import finish_call, logged_call
from app.services.finetuned_frame_matching import segment_images_finetuned_detect
from app.services.storage import storage_service


router = APIRouter(prefix="/api/semantic-frames", tags=["semantic-frames"])

SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


@router.post("/image")
async def segment_uploaded_image(file: UploadFile = File(...)):
    filename = file.filename or "image.png"
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_IMAGE_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"Unsupported image format: {suffix or 'unknown'}")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded image is empty")

    image_id = uuid.uuid4().hex
    safe_name = safe_filename(filename)
    source_key = f"semantic-frames/images/{image_id}/source/{safe_name}"
    bbox_image_key = f"semantic-frames/images/{image_id}/bbox.jpg"
    mask_image_key = f"semantic-frames/images/{image_id}/mask.jpg"
    result_json_key = f"semantic-frames/images/{image_id}/result.json"

    with VideoSessionLocal() as session:
        with logged_call(
            session,
            interface_type="rest",
            tool_or_endpoint="POST /api/semantic-frames/image",
            request={"filename": filename, "size_bytes": len(content)},
        ) as call_id:
            try:
                result = segment_images_finetuned_detect(image_files=[(safe_name, content)])[0]
                source = storage_service.upload_bytes(
                    object_key=source_key,
                    content=content,
                    media_type=file.content_type or media_type_for_suffix(suffix),
                )
                bbox_image = storage_service.upload_bytes(
                    object_key=bbox_image_key,
                    content=base64.b64decode(result.bbox_image_base64),
                    media_type="image/jpeg",
                )
                mask_image = storage_service.upload_bytes(
                    object_key=mask_image_key,
                    content=base64.b64decode(result.mask_image_base64),
                    media_type="image/jpeg",
                )
                payload = {
                    "image_id": image_id,
                    "source": stored_object_payload(source),
                    "bbox_image": stored_object_payload(bbox_image),
                    "bbox_image_url": storage_service.url_for(bbox_image.object_key),
                    "bbox_image_object_key": bbox_image.object_key,
                    "mask_image": stored_object_payload(mask_image),
                    "mask_image_url": storage_service.url_for(mask_image.object_key),
                    "mask_image_object_key": mask_image.object_key,
                    "result_json_url": storage_service.url_for(result_json_key),
                    "result_json_object_key": result_json_key,
                    "objects": result.objects,
                    "quality": result.quality,
                    "mask_available": result.mask_available,
                    "model_info": {
                        "backend": "finetuned_yolo_world_sam",
                        "display_name": "本地 yolov8s-world + sam2.1_b.pt",
                        "detection_model": "本地 yolov8s-world",
                        "mask_model": "sam2.1_b.pt",
                        "yolo_world_model": settings.video_graph_index_finetuned_yolo_world_model,
                        "mobile_sam_model": result.sam_model or settings.video_graph_index_mobile_sam_model,
                        "yolo_world_device": result.yolo_world_device,
                        "mobile_sam_device": result.mobile_sam_device,
                        "confidence": settings.video_graph_index_yolo_world_confidence,
                        "iou": settings.video_graph_index_yolo_world_iou,
                        "max_det": settings.video_graph_index_yolo_world_max_det,
                    },
                }
                result_json = storage_service.upload_bytes(
                    object_key=result_json_key,
                    content=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
                    media_type="application/json; charset=utf-8",
                )
                payload["result_json"] = stored_object_payload(result_json)
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            finish_call(session, call_id, payload)
            return payload


def stored_object_payload(stored) -> dict:
    return {
        "bucket": stored.bucket,
        "object_key": stored.object_key,
        "url": storage_service.url_for(stored.object_key),
        "media_type": stored.media_type,
        "size_bytes": stored.size_bytes,
        "checksum": stored.checksum,
    }


def media_type_for_suffix(suffix: str) -> str:
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".bmp":
        return "image/bmp"
    return "application/octet-stream"


def safe_filename(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value) or "image.png"
