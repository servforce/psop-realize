from __future__ import annotations

from pathlib import Path

from app.core.video_config import video_settings as settings
from app.models.video import VideoFrame
from app.services.asr import local_asr_client


class VideoJobRunner:
    def run(self, video_id: str) -> None:
        from app.services.video_parsing import parse_full

        parse_full(video_id)


def media_type_for_suffix(suffix: str) -> str:
    return {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
    }.get(suffix.lower(), "application/octet-stream")


def transcribe_or_fallback(source_path: Path, filename: str, object_key: str, frames: list[VideoFrame]) -> dict:
    if not local_asr_client.enabled():
        raise RuntimeError("LOCAL_ASR_API_BASE_URL is not configured")
    result = local_asr_client.transcribe_video_file(
        path=source_path,
        media_type=media_type_for_suffix(source_path.suffix),
        language=settings.local_asr_language,
    )
    return {
        "language": result.language,
        "provider": "local_video",
        "raw_response": result.raw_response,
    }


def extract_keyframes_like_vidnote(video_path: Path, frame_dir: Path, max_keyframes: int) -> list[Path]:
    frame_dir.mkdir(parents=True, exist_ok=True)
    duration_ms = probe_video_duration_ms(video_path)
    scene_timestamps = detect_scene_change_timestamps_ms(video_path)
    timestamps = build_frame_timestamps_ms(duration_ms=duration_ms, scene_timestamps_ms=scene_timestamps, max_keyframes=max_keyframes)
    outputs = []
    for timestamp_ms in timestamps[:max_keyframes]:
        output = frame_dir / f"{timestamp_ms:09d}.jpg"
        if extract_frame_at_timestamp(video_path=video_path, output_path=output, timestamp_ms=timestamp_ms):
            outputs.append(output)
    if outputs:
        return outputs
    output = frame_dir / "000000000.jpg"
    if extract_first_frame(video_path=video_path, output_path=output):
        return [output]
    write_placeholder_jpeg(output)
    return [output]


def extract_dense_frames(video_path: Path, frame_dir: Path, *, interval_seconds: float) -> list[Path]:
    frame_dir.mkdir(parents=True, exist_ok=True)
    duration_ms = probe_video_duration_ms(video_path)
    timestamps = dense_sample_timestamps_ms(duration_ms=duration_ms, interval_seconds=interval_seconds)
    return extract_frames_at_timestamps(video_path=video_path, frame_dir=frame_dir, timestamps_ms=timestamps)


def extract_candidate_frames(video_path: Path, frame_dir: Path, *, interval_seconds: float) -> list[Path]:
    frame_dir.mkdir(parents=True, exist_ok=True)
    duration_ms = probe_video_duration_ms(video_path)
    scene_timestamps: list[int] = []
    if settings.video_scene_change_frame_enabled:
        scene_timestamps = detect_scene_change_timestamps_ms(video_path)
    timestamps = build_candidate_frame_timestamps_ms(
        duration_ms=duration_ms,
        interval_seconds=interval_seconds,
        scene_timestamps_ms=scene_timestamps,
        min_spacing_ms=settings.video_candidate_frame_min_spacing_ms,
    )
    return extract_frames_at_timestamps(video_path=video_path, frame_dir=frame_dir, timestamps_ms=timestamps)


def extract_frames_at_timestamps(*, video_path: Path, frame_dir: Path, timestamps_ms: list[int]) -> list[Path]:
    outputs = []
    for timestamp_ms in timestamps_ms:
        output = frame_dir / f"{timestamp_ms:09d}.jpg"
        if extract_frame_at_timestamp(video_path=video_path, output_path=output, timestamp_ms=timestamp_ms):
            outputs.append(output)
    if outputs:
        return outputs
    output = frame_dir / "000000000.jpg"
    if extract_first_frame(video_path=video_path, output_path=output):
        return [output]
    write_placeholder_jpeg(output)
    return [output]


def build_candidate_frame_timestamps_ms(
    *,
    duration_ms: int,
    interval_seconds: float,
    scene_timestamps_ms: list[int],
    min_spacing_ms: int,
) -> list[int]:
    dense = dense_sample_timestamps_ms(duration_ms=duration_ms, interval_seconds=interval_seconds)
    valid_scene = [
        int(timestamp)
        for timestamp in scene_timestamps_ms
        if timestamp >= 0 and (duration_ms <= 0 or timestamp < duration_ms)
    ]
    return merge_close_timestamps_ms([*dense, *valid_scene], min_spacing_ms=min_spacing_ms)


def dense_sample_timestamps_ms(*, duration_ms: int, interval_seconds: float) -> list[int]:
    interval_ms = max(500, int(round(max(0.1, interval_seconds) * 1000)))
    if duration_ms <= 0:
        return [0]
    values = list(range(0, max(1, duration_ms), interval_ms))
    final_timestamp = max(0, duration_ms - 1000)
    if final_timestamp and (not values or final_timestamp - values[-1] > interval_ms // 2):
        values.append(final_timestamp)
    return sorted(set(values)) or [0]


def merge_close_timestamps_ms(timestamps_ms: list[int], *, min_spacing_ms: int) -> list[int]:
    spacing = max(0, int(min_spacing_ms))
    merged: list[int] = []
    for timestamp_ms in sorted(set(max(0, int(value)) for value in timestamps_ms)):
        if merged and timestamp_ms - merged[-1] < spacing:
            continue
        merged.append(timestamp_ms)
    return merged or [0]


def generate_analysis_proxy_video(source_path: Path, output_path: Path) -> None:
    import subprocess

    output_path.parent.mkdir(parents=True, exist_ok=True)
    height = max(144, int(settings.video_analysis_proxy_height or 720))
    crf = max(0, min(51, int(settings.video_analysis_proxy_crf or 28)))
    preset = settings.video_analysis_proxy_preset or "medium"
    audio_bitrate = settings.video_analysis_proxy_audio_bitrate or "128k"
    command = [
        ffmpeg_exe(),
        "-y",
        "-i",
        str(source_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        f"scale=-2:{height}",
        "-c:v",
        "libx265",
        "-tag:v",
        "hvc1",
        "-crf",
        str(crf),
        "-preset",
        preset,
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    result = subprocess.run(command, check=False, capture_output=True, timeout=3600)
    if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
        stderr = result.stderr.decode("utf-8", errors="replace")[-4000:]
        raise RuntimeError(f"Failed to generate 720P H.265 analysis proxy video: {stderr}")


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def probe_video_duration_ms(video_path: Path) -> int:
    import re
    import subprocess

    result = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(video_path)], check=False, capture_output=True)
    output = result.stderr.decode("utf-8", errors="replace")
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    if not match:
        return 0
    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return int(round(((hours * 60 + minutes) * 60 + seconds) * 1000))


def timeline_sample_timestamps_ms(*, duration_ms: int, max_keyframes: int) -> list[int]:
    if duration_ms <= 0 or max_keyframes <= 0:
        return [0]
    target = max(1, min(60, max_keyframes))
    interval_ms = max(30_000, int((duration_ms + target - 1) / target))
    values = list(range(0, max(1, duration_ms), interval_ms))[:target]
    final_timestamp = max(0, duration_ms - 1000)
    if values and final_timestamp - values[-1] > interval_ms // 2:
        if len(values) < target:
            values.append(final_timestamp)
        else:
            values[-1] = final_timestamp
    return sorted(set(values))


def detect_scene_change_timestamps_ms(video_path: Path) -> list[int]:
    import re
    import subprocess

    threshold = max(0.0, min(1.0, float(settings.video_scene_change_threshold or 0.25)))
    command = [
        ffmpeg_exe(),
        "-hide_banner",
        "-i",
        str(video_path),
        "-an",
        "-vf",
        f"scale=320:-2,select='gt(scene,{threshold})',showinfo",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(command, check=False, capture_output=True, timeout=120)
    except Exception:
        return []
    output = result.stderr.decode("utf-8", errors="replace")
    return [int(round(float(value) * 1000)) for value in re.findall(r"pts_time:([0-9]+(?:\.[0-9]+)?)", output)]


def build_frame_timestamps_ms(*, duration_ms: int, scene_timestamps_ms: list[int], max_keyframes: int) -> list[int]:
    timeline = timeline_sample_timestamps_ms(duration_ms=duration_ms, max_keyframes=max_keyframes)
    remaining = max(0, max_keyframes - len(timeline))
    scene = select_scene_timestamps_ms(scene_timestamps_ms=scene_timestamps_ms, duration_ms=duration_ms, limit=remaining)
    return merge_timestamps([*timeline, *scene], max_keyframes=max_keyframes)


def select_scene_timestamps_ms(*, scene_timestamps_ms: list[int], duration_ms: int, limit: int) -> list[int]:
    if limit <= 0:
        return []
    buckets: dict[int, list[int]] = {}
    for timestamp_ms in sorted(set(scene_timestamps_ms)):
        if timestamp_ms < 0 or (duration_ms > 0 and timestamp_ms >= duration_ms):
            continue
        bucket = timestamp_ms // 60_000
        bucket_items = buckets.setdefault(bucket, [])
        if len(bucket_items) < 2:
            bucket_items.append(timestamp_ms)
    selected: list[int] = []
    for bucket in sorted(buckets):
        for timestamp_ms in buckets[bucket]:
            selected.append(timestamp_ms)
            if len(selected) >= limit:
                return selected
    return selected


def merge_timestamps(timestamps: list[int], *, max_keyframes: int) -> list[int]:
    selected: list[int] = []
    for timestamp_ms in sorted(set(timestamps)):
        if any(abs(existing - timestamp_ms) <= 5_000 for existing in selected):
            continue
        selected.append(timestamp_ms)
        if len(selected) >= max_keyframes:
            break
    return selected or [0]


def extract_frame_at_timestamp(*, video_path: Path, output_path: Path, timestamp_ms: int) -> bool:
    import subprocess

    command = [
        ffmpeg_exe(),
        "-y",
        "-ss",
        f"{timestamp_ms / 1000:.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        "-vf",
        "scale=960:-2",
        str(output_path),
    ]
    try:
        result = subprocess.run(command, check=False, capture_output=True, timeout=60)
    except Exception:
        return False
    return result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0


def extract_first_frame(*, video_path: Path, output_path: Path) -> bool:
    import subprocess

    command = [ffmpeg_exe(), "-y", "-i", str(video_path), "-vf", "scale=960:-2", "-frames:v", "1", str(output_path)]
    try:
        result = subprocess.run(command, check=False, capture_output=True, timeout=60)
    except Exception:
        return False
    return result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0


def write_placeholder_jpeg(output: Path) -> None:
    output.write_bytes(
        bytes.fromhex(
            "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f"
            "141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101"
            "011100ffc4001400010000000000000000000000000000000000000000ffda0008010100003f00d2cf20ffd9"
        )
    )


def parse_timestamp_from_frame_name(name: str) -> int:
    try:
        return int(Path(name).stem)
    except ValueError:
        return 0


def format_timestamp(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
