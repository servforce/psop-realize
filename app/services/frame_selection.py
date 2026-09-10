from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import Any

from app.core.video_config import video_settings as settings
from app.models.video import VideoFrame


SELECTED = "selected"
REJECTED = "rejected"


@dataclass
class QualityResult:
    passed: bool
    reason: str
    details: dict[str, Any]


def mark_frame_selected(
    frame: VideoFrame,
    *,
    score: float,
    reason: str,
    details: dict[str, Any],
    matched_section_index: int | None,
) -> None:
    frame.selection_status = SELECTED
    frame.selection_score = max(0.0, min(1.0, float(score)))
    frame.selection_reason = reason[:4000]
    frame.selection_details_json = json.dumps(details, ensure_ascii=False, default=str)
    frame.matched_section_index = matched_section_index


def mark_frame_rejected(
    frame: VideoFrame,
    *,
    score: float,
    reason: str,
    details: dict[str, Any],
    matched_section_index: int | None,
) -> None:
    frame.selection_status = REJECTED
    frame.selection_score = max(0.0, min(1.0, float(score)))
    frame.selection_reason = reason[:4000]
    frame.selection_details_json = json.dumps(details, ensure_ascii=False, default=str)
    frame.matched_section_index = matched_section_index


def analyze_image_quality(image_bytes: bytes) -> QualityResult:
    try:
        details = analyze_quality_with_opencv(image_bytes)
    except Exception:
        details = analyze_quality_with_pillow(image_bytes)

    blur_score = float(details.get("blur_score") or 0.0)
    mean_brightness = float(details.get("mean_brightness") or 0.0)
    dark_ratio = float(details.get("dark_ratio") or 0.0)
    bright_ratio = float(details.get("bright_ratio") or 0.0)

    failures: list[str] = []
    if blur_score < settings.video_frame_selection_min_blur_score:
        failures.append("severe_blur")
    if dark_ratio >= settings.video_frame_selection_dark_ratio_threshold or mean_brightness <= settings.video_frame_selection_min_mean_brightness:
        failures.append("too_dark_or_black")
    if bright_ratio >= settings.video_frame_selection_bright_ratio_threshold or mean_brightness >= settings.video_frame_selection_max_mean_brightness:
        failures.append("too_bright_or_overexposed")

    details["failures"] = failures
    if failures:
        return QualityResult(
            passed=False,
            reason=f"Rejected by local quality filter: {', '.join(failures)}.",
            details=details,
        )
    return QualityResult(
        passed=True,
        reason="Passed local quality checks.",
        details=details,
    )


def analyze_quality_with_opencv(image_bytes: bytes) -> dict[str, Any]:
    import cv2
    import numpy as np

    buffer = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("OpenCV failed to decode image")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return {
        "method": "opencv_laplacian",
        "width": int(image.shape[1]),
        "height": int(image.shape[0]),
        "blur_score": float(cv2.Laplacian(gray, cv2.CV_64F).var()),
        "mean_brightness": float(gray.mean()),
        "dark_ratio": float((gray < 20).mean()),
        "bright_ratio": float((gray > 245).mean()),
    }


def analyze_quality_with_pillow(image_bytes: bytes) -> dict[str, Any]:
    from PIL import Image, ImageFilter, ImageStat

    with Image.open(io.BytesIO(image_bytes)) as image:
        gray = image.convert("L")
        histogram = gray.histogram()
        total = max(1, sum(histogram))
        mean_brightness = float(ImageStat.Stat(gray).mean[0])
        edge = gray.filter(ImageFilter.FIND_EDGES)
        blur_score = float(ImageStat.Stat(edge).var[0])
        return {
            "method": "pillow_edge_fallback",
            "width": int(image.width),
            "height": int(image.height),
            "blur_score": blur_score,
            "mean_brightness": mean_brightness,
            "dark_ratio": sum(histogram[:20]) / total,
            "bright_ratio": sum(histogram[246:]) / total,
        }
