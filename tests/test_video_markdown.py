import importlib.util
import sys
import types


if importlib.util.find_spec("httpx") is None:
    sys.modules["httpx"] = types.ModuleType("httpx")

from app.services.transcript_tree import attach_semantic_frame_report_to_transcript_tree, build_markdown_from_transcript_tree


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
                    "polished_text": "首先打开变频器盖板，将交流 220 伏电源接到输入端。",
                    "business_frame_text": "变频器盖板打开，输入端子和已接入的交流 220 伏电源线清晰可见。",
                    "frames": [
                        {
                            "id": 11,
                            "timestamp_time": "00:42",
                            "caption": "视频 00:42 关键帧",
                            "object_key": "videos/video-1/frames/000042000.jpg",
                            "url": "/api/videos/video-1/frames/000042000.jpg",
                        },
                        {
                            "id": 12,
                            "timestamp_time": "00:55",
                            "caption": "视频 00:55 关键帧",
                            "object_key": "videos/video-1/frames/000055000.jpg",
                            "url": "/api/videos/video-1/frames/000055000.jpg",
                        }
                    ],
                    "business_frames": [
                        {
                            "id": 12,
                            "timestamp_time": "00:55",
                            "caption": "视频 00:55 业务帧",
                            "object_key": "videos/video-1/frames/000055000.jpg",
                            "url": "/api/videos/video-1/frames/000055000.jpg",
                            "score": 0.91,
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

    markdown = build_markdown_from_transcript_tree(tree=tree)

    assert 'document_role: "video_analysis_result"' in markdown
    assert 'video_id: "video-1"' in markdown
    # 存储对象引用不再写入文档,避免暴露存储实现
    assert "transcript_tree_object:" not in markdown
    assert "source_video_object:" not in markdown
    assert "# 变频器接线演示" in markdown
    assert "## 1. 打开盖板接入电源" in markdown
    assert "**时间范围：** 00:35 - 01:10" in markdown
    assert "### 润色后正文" in markdown
    assert "交流 220 伏电源" in markdown
    assert "### 业务帧" in markdown
    assert "视频 00:55 业务帧" in markdown
    # 选中分数最高的帧:仍由 url 断言,object_key 行已移除
    assert "/api/videos/video-1/frames/000055000.jpg" in markdown
    assert "相似度分数：0.9100" in markdown
    assert "000042000.jpg" not in markdown
    assert "  - 路径：" not in markdown
    assert "### 待匹配文本" not in markdown
    assert "### 关键帧" not in markdown
    assert "### 提取后业务帧" not in markdown
    assert "### 线框图" not in markdown
    assert "videos/video-1/wireframes/000042000.png" not in markdown
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

    markdown = build_markdown_from_transcript_tree(tree=tree)

    assert "# 空媒体示例" in markdown
    assert "## 1. 说明段落" in markdown
    assert "这一段暂时没有对应的关键帧和线框图。" in markdown
    assert "### 业务帧\n\n- 无" in markdown


def test_markdown_uses_business_frame_from_semantic_report():
    tree = {
        "video": {"id": "video-3", "title": "业务帧示例", "filename": "semantic.mp4", "duration": "00:40"},
        "tree": {
            "title": "业务帧示例",
            "sections": [
                {
                    "index": 1,
                    "title": "安装底座",
                    "start_time": "00:00",
                    "end_time": "00:40",
                    "polished_text": "将底座固定到安装位置。",
                }
            ],
        },
    }
    semantic_report = {
        "sections": [
            {
                "index": 1,
                "query_graph_matches": [
                    {
                        "frames": [
                            {
                                "id": 31,
                                "timestamp_time": "00:12",
                                "caption": "低分关键帧",
                                "object_key": "videos/video-3/frames/000012000.jpg",
                                "url": "/api/videos/video-3/frames/000012000.jpg",
                                "score": 0.44,
                            },
                            {
                                "id": 32,
                                "timestamp_time": "00:18",
                                "caption": "高分关键帧",
                                "object_key": "videos/video-3/frames/000018000.jpg",
                                "url": "/api/videos/video-3/frames/000018000.jpg",
                                "score": 0.88,
                            },
                        ]
                    }
                ],
            }
        ]
    }

    semantic_report["sections"][0]["raw_frames"] = [
        {
            "id": 30,
            "timestamp_time": "00:08",
            "caption": "原始 FFmpeg 关键帧",
            "object_key": "videos/video-3/frames/000008000.jpg",
            "url": "/api/videos/video-3/frames/000008000.jpg",
        }
    ]

    attach_semantic_frame_report_to_transcript_tree(tree=tree, semantic_report=semantic_report)
    markdown = build_markdown_from_transcript_tree(tree=tree)

    assert "原始 FFmpeg 关键帧" not in markdown
    assert "000008000.jpg" not in markdown
    assert "高分关键帧" in markdown
    assert "低分关键帧" not in markdown
    assert "相似度分数：0.8800" in markdown
    assert "/api/videos/video-3/frames/000018000.jpg" in markdown


def test_export_markdown_uses_package_relative_frame_paths():
    from app.services.video_export import FRAMES_DIR, package_frame_url

    tree = {
        "video": {"id": "video-4", "title": "导出示例", "filename": "export.mp4", "duration": "00:20"},
        "tree": {
            "title": "导出示例",
            "sections": [
                {
                    "index": 1,
                    "title": "接线",
                    "start_time": "00:00",
                    "end_time": "00:20",
                    "polished_text": "把线接到端子上。",
                    "business_frames": [
                        {
                            "id": 41,
                            "timestamp_time": "00:09",
                            "caption": "端子接线",
                            "object_key": "videos/video-4/frames/000009000.jpg",
                            "url": "/api/videos/video-4/frames/000009000.jpg",
                            "score": 0.77,
                        }
                    ],
                }
            ],
        },
    }
    local_names = {"videos/video-4/frames/000009000.jpg": "000009000.jpg"}

    markdown = build_markdown_from_transcript_tree(
        tree=tree,
        frame_url=lambda frame: package_frame_url(frame, local_names),
        embed_frames=True,
    )

    # 包内相对路径,且带 ./ 前缀以通过 workbench 的 safeMarkdownUrl 白名单
    assert f"![端子接线](./{FRAMES_DIR}/000009000.jpg)" in markdown
    # 不泄漏 API 路径和存储 key
    assert "/api/videos/" not in markdown
    assert "videos/video-4/frames/" not in markdown
