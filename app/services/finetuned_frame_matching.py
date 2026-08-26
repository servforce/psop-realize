from __future__ import annotations

import base64
import io
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app.core.video_config import video_settings as settings
from app.services.semantic_frames import (
    analyze_image_quality,
    build_detection_objects,
    detect_prompt_objects,
    load_image_metadata,
    load_mobile_sam_model,
    load_yolo_world_model,
    metrics_from_mask,
)


logger = logging.getLogger(__name__)


@dataclass
class SegmentationResult:
    bbox_image_base64: str
    mask_image_base64: str
    filename: str
    objects: list[dict[str, Any]]
    quality: dict[str, Any]
    mask_available: bool
    mobile_sam_device: str = ""
    yolo_world_device: str = ""
    sam_model: str = ""


def segment_images_finetuned_detect(
    image_files: list[tuple[str, bytes]],
) -> list[SegmentationResult]:
    logger.info(
        "finetuned frame matching devices: SAM=%s YOLO-World=%s model=%s images=%d",
        settings.video_graph_index_device,
        settings.video_graph_index_device,
        settings.video_graph_index_finetuned_yolo_world_model,
        len(image_files),
    )
    yolo_world = load_yolo_world_model(settings.video_graph_index_finetuned_yolo_world_model)
    mobile_sam = load_mobile_sam_model(
        settings.video_graph_index_mobile_sam_model,
        device=settings.video_graph_index_device,
    )
    _move_model_to_device(yolo_world, settings.video_graph_index_device)

    results: list[SegmentationResult] = []
    for filename, image_bytes in image_files:
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=f"_{filename}", delete=False) as tmp:
                temp_path = Path(tmp.name)
                tmp.write(image_bytes)

            quality_result = analyze_image_quality(image_bytes)
            image_size = load_image_metadata(temp_path)["size"]
            detections = detect_prompt_objects(
                model=yolo_world,
                image_path=temp_path,
                prompt_terms=[],
                confidence=settings.video_graph_index_yolo_world_confidence,
                iou=settings.video_graph_index_yolo_world_iou,
                max_det=settings.video_graph_index_yolo_world_max_det,
                device=settings.video_graph_index_device,
            )
            objects = build_detection_objects(detections=detections, image_size=image_size)
            mask_result = _predict_mask_data_with_scores(
                model=mobile_sam,
                image_path=temp_path,
                detections=detections,
                device=settings.video_graph_index_device,
            )
            mask_data = mask_result[0] if mask_result else None
            mask_scores = mask_result[1] if mask_result else []
            mask_available = False
            if mask_data:
                objects = _merge_mask_metrics(
                    objects=objects,
                    mask_data=mask_data,
                    image_size=image_size,
                    mask_scores=mask_scores,
                )
                mask_available = True

            bbox_image = draw_bbox_segmentation_result(image_bytes=image_bytes, objects=objects)
            mask_image = (
                draw_mask_segmentation_result(image_bytes=image_bytes, objects=objects, mask_data=mask_data)
                if mask_available
                else bbox_image.copy()
            )

            results.append(
                SegmentationResult(
                    bbox_image_base64=_encode_image(bbox_image),
                    mask_image_base64=_encode_image(mask_image),
                    filename=filename,
                    objects=objects,
                    quality=quality_result.details,
                    mask_available=mask_available,
                    mobile_sam_device=settings.video_graph_index_device,
                    yolo_world_device=settings.video_graph_index_device,
                    sam_model=settings.video_graph_index_mobile_sam_model,
                )
            )
            logger.info(
                "finetuned frame matching image done: filename=%s model=%s objects=%d mask=%s",
                filename,
                settings.video_graph_index_finetuned_yolo_world_model,
                len(objects),
                mask_available,
            )
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    return results


def draw_bbox_segmentation_result(
    image_bytes: bytes,
    objects: list[dict[str, Any]],
) -> Image.Image:
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(image)
    font_small = _load_label_font()

    for index, obj in enumerate(objects):
        color = _label_color(index)
        label = str(obj.get("label") or "unknown")
        confidence = float(obj.get("confidence") or 0.0)
        bbox = obj.get("bbox") or []
        if len(bbox) < 4:
            continue

        x1, y1, x2, y2 = [float(value) for value in bbox[:4]]
        draw.rectangle([x1, y1, x2, y2], outline=color, width=3)
        label_text = f"{label} ({confidence:.2%})"
        text_box = draw.textbbox((x1, y1 - 20), label_text, font=font_small)
        draw.rectangle(text_box, fill=color)
        draw.text((x1, y1 - 20), label_text, fill=(255, 255, 255), font=font_small)

    return image


def draw_mask_segmentation_result(
    image_bytes: bytes,
    objects: list[dict[str, Any]],
    mask_data: Any,
) -> Image.Image:
    import numpy as np

    base_image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    overlay = Image.new("RGBA", base_image.size, (0, 0, 0, 0))
    font_small = _load_label_font()

    masks = list(mask_data or [])
    has_mask = False
    for index, _obj in enumerate(objects):
        mask = masks[index] if index < len(masks) else None
        if mask is None:
            continue
        mask_array = np.asarray(mask)
        if mask_array.ndim > 2:
            mask_array = mask_array.squeeze()
        if not mask_array.size:
            continue
        mask_binary = (mask_array > 0.5).astype(np.uint8) * 255
        mask_image = Image.fromarray(mask_binary, mode="L")
        if mask_image.size != base_image.size:
            mask_image = mask_image.resize(base_image.size, resample=Image.NEAREST)
        color_layer = Image.new("RGBA", base_image.size, (*_label_color(index), 92))
        overlay = Image.composite(color_layer, overlay, mask_image)
        has_mask = True

    if has_mask:
        base_image = Image.alpha_composite(base_image, overlay)

    draw = ImageDraw.Draw(base_image)
    for index, obj in enumerate(objects):
        bbox = obj.get("bbox") or []
        if len(bbox) < 4:
            continue
        x1, y1, x2, y2 = [float(value) for value in bbox[:4]]
        color = _label_color(index)
        draw.rectangle([x1, y1, x2, y2], outline=color, width=2)
        label = str(obj.get("label") or "unknown")
        if obj.get("mask_confidence") is not None:
            label_text = f"{label} mask ({float(obj.get('mask_confidence') or 0.0):.2%})"
        else:
            label_text = f"{label} ({float(obj.get('confidence') or 0.0):.2%})"
        text_box = draw.textbbox((x1, y1 - 20), label_text, font=font_small)
        draw.rectangle(text_box, fill=color)
        draw.text((x1, y1 - 20), label_text, fill=(255, 255, 255), font=font_small)

    return base_image.convert("RGB")


def _predict_mask_data_with_scores(
    *,
    model: Any,
    image_path: Path,
    detections: dict[str, Any],
    device: str,
) -> tuple[list[Any], list[float | None]] | None:
    detection_items = detections.get("items") if isinstance(detections, dict) else []
    if not isinstance(detection_items, list) or not detection_items:
        return None

    bboxes = [item.get("bbox") for item in detection_items if isinstance(item, dict) and isinstance(item.get("bbox"), list)]
    if not bboxes:
        return None

    results = model.predict(
        source=str(image_path),
        bboxes=bboxes,
        device=device,
        retina_masks=True,
        verbose=False,
    )
    result = results[0] if results else None
    masks = getattr(result, "masks", None)
    if masks is None or getattr(masks, "data", None) is None:
        return None

    mask_data = masks.data
    if hasattr(mask_data, "detach"):
        mask_data = mask_data.detach().cpu().numpy()
    else:
        try:
            mask_data = mask_data.cpu().numpy()
        except Exception:
            return None
    mask_list = list(mask_data)
    return mask_list, _extract_sam_mask_scores(result=result, masks=masks, count=len(mask_list))


def _extract_sam_mask_scores(*, result: Any, masks: Any, count: int) -> list[float | None]:
    candidates = [
        getattr(masks, "conf", None),
        getattr(masks, "scores", None),
        getattr(result, "scores", None),
        getattr(getattr(result, "boxes", None), "conf", None),
    ]
    for index, candidate in enumerate(candidates):
        scores = _numeric_sequence(candidate)
        if scores:
            if index == len(candidates) - 1 and all(abs(score - 1.0) < 1e-9 for score in scores):
                return [None for _ in range(count)]
            return [scores[item_index] if item_index < len(scores) else None for item_index in range(count)]
    return [None for _ in range(count)]


def _numeric_sequence(value: Any) -> list[float]:
    if value is None:
        return []
    try:
        if hasattr(value, "detach"):
            value = value.detach().cpu().tolist()
        elif hasattr(value, "cpu"):
            value = value.cpu().tolist()
        elif hasattr(value, "tolist"):
            value = value.tolist()
    except Exception:
        return []
    if not isinstance(value, list):
        value = [value]
    scores: list[float] = []
    for item in value:
        if isinstance(item, list):
            if not item:
                continue
            item = item[0]
        try:
            scores.append(float(item))
        except (TypeError, ValueError):
            continue
    return scores


def _merge_mask_metrics(
    *,
    objects: list[dict[str, Any]],
    mask_data: list[Any],
    image_size: dict[str, int],
    mask_scores: list[float | None] | None = None,
) -> list[dict[str, Any]]:
    width = max(1, int(image_size.get("width") or 0))
    height = max(1, int(image_size.get("height") or 0))
    merged: list[dict[str, Any]] = []
    for index, item in enumerate(objects):
        merged_item = dict(item)
        if index < len(mask_data):
            mask_metrics = metrics_from_mask(mask_data[index], width=width, height=height)
            mask_score = mask_scores[index] if mask_scores and index < len(mask_scores) else None
            merged_item.update(
                {
                    "mask_bbox": [float(value) for value in mask_metrics["bbox"][:4]],
                    "mask_area_ratio": float(mask_metrics["area_ratio"]),
                    "centroid_x": float(mask_metrics["centroid_x"]),
                    "centroid_y": float(mask_metrics["centroid_y"]),
                    "touches_border": bool(mask_metrics["touches_border"]),
                }
            )
            if mask_score is not None:
                merged_item["mask_confidence"] = float(mask_score)
        merged.append(merged_item)
    return merged


def _encode_image(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def _move_model_to_device(model: Any, device: str) -> None:
    if hasattr(model, "to"):
        try:
            model.to(device)
        except Exception:
            pass


def _load_label_font() -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", 12)
    except Exception:
        return ImageFont.load_default()


def _label_color(index: int) -> tuple[int, int, int]:
    colors = [
        (255, 0, 0),
        (0, 255, 0),
        (0, 0, 255),
        (255, 255, 0),
        (255, 0, 255),
        (0, 255, 255),
        (255, 128, 0),
        (128, 0, 255),
    ]
    return colors[index % len(colors)]
