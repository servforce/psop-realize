import importlib.util
import sys
import types


if importlib.util.find_spec("httpx") is None:
    sys.modules["httpx"] = types.ModuleType("httpx")

from app.services.transcript_tree import build_markdown_from_transcript_tree


def test_build_markdown_from_transcript_tree_renders_structured_sections():
    tree = {
        "video": {
            "id": "video-1",
            "title": "变频器接线演示",
            "filename": "inverter.mp4",
            "duration": "03:20",
        },
        "source": {
            "tree_object_key": "videos/video-1/transcript/transcript_tree.json",
        },
        "tree": {
            "title": "变频器接线演示",
            "sections": [
                {
                    "index": 1,
                    "title": "打开盖板接入电源",
                    "start_time": "00:35",
                    "end_time": "01:10",
                    "text": "首先打开变频器盖板，将交流二百二十伏电源接到输入端。",
                    "frames": [
                        {
                            "id": 11,
                            "timestamp_time": "00:42",
                            "caption": "视频 00:42 关键帧",
                            "object_key": "videos/video-1/frames/000042000.jpg",
                            "url": "/api/videos/video-1/frames/000042000.jpg",
                        }
                    ],
                    "wireframes": [
                        {
                            "frame_id": 11,
                            "timestamp_time": "00:42",
                            "object_key": "videos/video-1/wireframes/000042000.png",
                            "url": "/api/objects/videos/video-1/wireframes/000042000.png",
                        }
                    ],
                }
            ],
        },
    }

    markdown = build_markdown_from_transcript_tree(
        tree=tree,
        source_video_object="videos/video-1/source/inverter.mp4",
    )

    assert 'document_role: "video_analysis_result"' in markdown
    assert 'video_id: "video-1"' in markdown
    assert 'transcript_tree_object: "videos/video-1/transcript/transcript_tree.json"' in markdown
    assert "# 视频分析结果：变频器接线演示" in markdown
    assert "## 1. 打开盖板接入电源" in markdown
    assert "**时间范围：** 00:35 - 01:10" in markdown
    assert "### 正文" in markdown
    assert "首先打开变频器盖板" in markdown
    assert "### 关键帧" in markdown
    assert "videos/video-1/frames/000042000.jpg" in markdown
    assert "### 线框图" in markdown
    assert "videos/video-1/wireframes/000042000.png" in markdown
    assert "## 完整转写" not in markdown


def test_build_markdown_from_transcript_tree_handles_empty_media():
    tree = {
        "video": {
            "id": "video-2",
            "title": "空媒体示例",
            "filename": "empty.mp4",
            "duration": "00:30",
        },
        "tree": {
            "title": "空媒体示例",
            "sections": [
                {
                    "index": 1,
                    "title": "说明段落",
                    "start_time": "00:00",
                    "end_time": "00:30",
                    "text": "这一段暂时没有对应的关键帧和线框图。",
                    "frames": [],
                    "wireframes": [],
                }
            ],
        },
    }

    markdown = build_markdown_from_transcript_tree(
        tree=tree,
        source_video_object="videos/video-2/source/empty.mp4",
    )

    assert "# 视频分析结果：空媒体示例" in markdown
    assert "## 1. 说明段落" in markdown
    assert "这一段暂时没有对应的关键帧和线框图。" in markdown
    assert "### 关键帧\n\n- 无" in markdown
    assert "### 线框图\n\n- 无" in markdown
