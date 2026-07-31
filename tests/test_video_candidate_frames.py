import subprocess
from pathlib import Path
from types import SimpleNamespace

from app.services import videos


def test_candidate_frame_timestamps_merge_dense_and_scene_changes():
    timestamps = videos.build_candidate_frame_timestamps_ms(
        duration_ms=11_000,
        interval_seconds=2.0,
        scene_timestamps_ms=[3_200, 7_800, 15_000],
        min_spacing_ms=500,
    )

    assert timestamps == [0, 2_000, 3_200, 4_000, 6_000, 7_800, 10_000]


def test_generate_analysis_proxy_video_uses_h265_720p_and_audio(monkeypatch, tmp_path):
    commands = []

    class Result:
        returncode = 0
        stderr = b""

    def fake_run(command, **kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"proxy")
        return Result()

    monkeypatch.setattr(videos, "ffmpeg_exe", lambda: "ffmpeg")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        videos,
        "settings",
        SimpleNamespace(
            video_analysis_proxy_height=720,
            video_analysis_proxy_crf=28,
            video_analysis_proxy_preset="medium",
            video_analysis_proxy_audio_bitrate="128k",
        ),
    )

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    output = tmp_path / "analysis_720p_h265.mp4"

    videos.generate_analysis_proxy_video(source, output)

    command = commands[0]
    assert output.read_bytes() == b"proxy"
    assert command[command.index("-c:v") + 1] == "libx265"
    assert command[command.index("-vf") + 1] == "scale=-2:720"
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-b:a") + 1] == "128k"
