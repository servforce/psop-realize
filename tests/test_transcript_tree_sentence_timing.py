import importlib.util
import sys
import types


if importlib.util.find_spec("httpx") is None:
    sys.modules["httpx"] = types.ModuleType("httpx")

from app.models.entities import VideoJob
from app.services.transcript_tree import extract_source_segments, normalize_transcript_tree


def test_extract_source_segments_prefers_sentences_over_coarser_segments():
    payload = {
        "segments": [
            {
                "text": "整段粗粒度文本。",
                "start": 0.0,
                "end": 10.0,
            }
        ],
        "sentences": [
            {
                "text": "第一句。",
                "start": 0.0,
                "end": 1.2,
            },
            {
                "text": "第二句。",
                "start": 1.2,
                "end": 2.4,
            },
        ],
    }

    segments = extract_source_segments(payload, duration_ms=2400)

    assert [segment["text"] for segment in segments] == ["第一句。", "第二句。"]
    assert [segment["start_seconds"] for segment in segments] == [0.0, 1.2]
    assert [segment["end_seconds"] for segment in segments] == [1.2, 2.4]


def test_normalize_transcript_tree_uses_asr_sentence_time_ranges():
    job = VideoJob(
        id="video-1",
        title="示例视频",
        filename="demo.mp4",
        source_bucket="bucket",
        source_object_key="videos/video-1/source/demo.mp4",
    )
    source_segments = [
        {"index": 0, "start_seconds": 0.0, "end_seconds": 1.0, "text": "第一句。"},
        {"index": 1, "start_seconds": 1.0, "end_seconds": 2.5, "text": "第二句。"},
        {"index": 2, "start_seconds": 2.5, "end_seconds": 4.0, "text": "第三句。"},
    ]
    tree = {
        "title": "qwen 润色后的标题",
        "sections": [
            {
                "title": "qwen 润色后的段落",
                "start_seconds": 99.0,
                "end_seconds": 100.0,
                "polished_text": "润色后的正文",
                "business_frame_text": "第一句、第二句和第三句对应的可见画面主体清晰。",
                "source_segment_indices": [0, 1, 2],
                "query_graph": {
                    "nodes": [
                        {"id": "subject", "label": "subject", "role": "part", "required": True, "weight": 1.0},
                    ],
                    "edges": [],
                },
            }
        ],
    }

    normalized = normalize_transcript_tree(
        tree=tree,
        job=job,
        source_segments=source_segments,
        frames=[],
        wireframes=[],
        duration_ms=4000,
    )

    section = normalized["tree"]["sections"][0]
    assert section["start_seconds"] == 0.0
    assert section["end_seconds"] == 4.0
    assert section["source_chunks"] == [0, 1, 2]
    assert section["text"] == "第一句。 第二句。 第三句。"
    assert section["polished_text"] == "润色后的正文"
    assert section["business_frame_text"] == "第一句、第二句和第三句对应的可见画面主体清晰。"
