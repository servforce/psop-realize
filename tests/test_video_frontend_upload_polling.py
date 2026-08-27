from pathlib import Path


FRONTEND_SCRIPT = Path("static/assets/video-app.js")
FRONTEND_HTML = Path("static/video.html")


def test_upload_ui_uses_combined_endpoint_and_client_task_id():
    source = FRONTEND_SCRIPT.read_text(encoding="utf-8-sig")

    assert 'form.append("task_id", taskId)' in source
    assert 'xhr.open("POST", "/api/videos/upload-and-parse")' in source
    assert 'xhr.open("POST", "/api/videos")' not in source
    assert "randomUUID()" in source
    assert "Math.random" not in source


def test_upload_ui_polls_status_every_two_seconds_until_terminal():
    source = FRONTEND_SCRIPT.read_text(encoding="utf-8-sig")

    assert "startUploadAndParsePolling(taskId, file.name, uploadAttempt)" in source
    assert "`/api/videos/${encodeURIComponent(taskId)}`" in source
    assert "setTimeout(poll, 2000)" in source
    assert '["completed", "completed_with_warnings", "failed"]' in source
    assert "uploadStatusPoll" in source
    assert "activeUploadAttempt" in source


def test_video_status_updates_are_monotonic_and_acknowledged():
    source = FRONTEND_SCRIPT.read_text(encoding="utf-8-sig")

    assert "if (!updateVideoInState(video)) return;" in source
    assert "if (!video?.id) return false;" in source
    assert "if (shouldIgnoreVideoUpdate(state.videos[index], video)) return false;" in source
    assert "return true;" in source


def test_video_page_uses_new_frontend_cache_version():
    html = FRONTEND_HTML.read_text(encoding="utf-8")

    assert "video-app.js?v=20260827-upload-parse-poll-v2" in html
