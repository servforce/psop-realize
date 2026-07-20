from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.models.entities import VideoFrame
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
    if local_asr_client.enabled():
        result = local_asr_client.transcribe_video_file(
            path=source_path,
            media_type=media_type_for_suffix(source_path.suffix),
            language=settings.local_asr_language,
        )
        return {
            "text": result.text,
            "language": result.language,
            "provider": "local_video",
            "raw_response": result.raw_response,
        }
    return {
        "text": build_placeholder_transcript(filename, frames),
        "language": settings.local_asr_language,
        "provider": "placeholder",
        "raw_response": {"source_object_key": object_key},
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

    command = [
        ffmpeg_exe(),
        "-hide_banner",
        "-i",
        str(video_path),
        "-an",
        "-vf",
        "scale=320:-2,select='gt(scene,0.25)',showinfo",
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


def build_placeholder_transcript(filename: str, frames: list[VideoFrame]) -> str:
    return (
        f"视频文件 {filename} 已完成第一版分析。"
        f"系统已提取 {len(frames)} 张关键帧。"
        "当前版本按需求先使用 ASR + 关键帧路线；未配置 ASR 服务时生成此占位转写。"
    )
