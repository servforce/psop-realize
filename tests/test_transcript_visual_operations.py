import importlib.util
import sys
import types
from types import SimpleNamespace


if importlib.util.find_spec("httpx") is None:
    sys.modules["httpx"] = types.ModuleType("httpx")

import app.services.transcript_tree as transcript_tree_module
from app.services.transcript_tree import (
    build_structured_transcript,
    build_transcript_raw_generation_info,
    build_transcript_structure_generation_info,
    normalize_transcript_tree,
    render_transcript_tree_text,
)


def test_normalize_transcript_tree_uses_asr_text_business_text_and_section_query_graph():
    job = SimpleNamespace(
        id="video-1",
        title="demo",
        filename="demo.mp4",
        source_bucket="bucket",
        source_object_key="videos/video-1/source/demo.mp4",
    )
    source_segments = [
        {"index": 0, "start_seconds": 0.0, "end_seconds": 1.0, "text": "Open the cabinet."},
        {"index": 1, "start_seconds": 1.0, "end_seconds": 3.0, "text": "Check the terminal labels."},
        {"index": 2, "start_seconds": 3.0, "end_seconds": 5.0, "text": "Connect the cable."},
    ]
    tree = {
        "title": "demo",
        "sections": [
            {
                "title": "Wiring",
                "source_segment_indices": [0, 1, 2],
                "polished_text": "Open the cabinet, check the terminal labels, and connect the cable.",
                "business_frame_text": "Terminal labels, wiring positions, and connected cable are clearly visible after wiring.",
                "query_graph": {
                    "nodes": [
                        {"id": "terminal", "label": "terminal label", "role": "part", "required": True, "weight": 0.6},
                        {"id": "wire", "label": "wire", "role": "part", "required": True, "weight": 0.4},
                    ],
                    "edges": [
                        {"from": "terminal", "relation": "near", "to": "wire", "required": False, "weight": 1.0},
                    ],
                },
            }
        ],
    }

    normalized = normalize_transcript_tree(
        tree=tree,
        job=job,
        source_segments=source_segments,
        frames=[],
        duration_ms=5000,
    )

    section = normalized["tree"]["sections"][0]
    assert section["start_seconds"] == 0.0
    assert section["end_seconds"] == 5.0
    assert section["text"] == "Open the cabinet. Check the terminal labels. Connect the cable."
    assert section["polished_text"] == "Open the cabinet, check the terminal labels, and connect the cable."
    assert section["business_frame_text"] == (
        "Terminal labels, wiring positions, and connected cable are clearly visible after wiring."
    )
    assert section["query_graph"]["nodes"][0]["id"] == "terminal"
    assert "state" not in section["query_graph"]
    assert "source_text" not in section
    assert "visual_operations" not in section
    assert "key_operations" not in section
    assert "frame_query" not in section
    assert "frame_queries" not in section

    rendered = render_transcript_tree_text(normalized)
    assert "Open the cabinet. Check the terminal labels. Connect the cable." in rendered
    assert "### 润色后正文" in rendered
    assert "Open the cabinet, check the terminal labels, and connect the cable." in rendered
    assert "### 待匹配文本" in rendered
    assert "Terminal labels, wiring positions, and connected cable are clearly visible after wiring." in rendered
    assert "frame_query" not in rendered
    assert "visual_operations" not in rendered


def test_transcript_generation_info_tracks_new_structure_version():
    job = SimpleNamespace(
        id="video-1",
        title="demo",
        filename="demo.mp4",
        source_bucket="bucket",
        source_object_key="videos/video-1/source/demo.mp4",
    )

    raw_generation = build_transcript_raw_generation_info(job=job)
    structure_generation = build_transcript_structure_generation_info(job=job)

    assert raw_generation["kind"] == "transcript_raw"
    assert raw_generation["source_object_key"] == job.source_object_key
    assert structure_generation["kind"] == "transcript_tree"
    assert structure_generation["version"] == "8"
    assert structure_generation["transcript_business_frame_mode"] == "section_polished_text_business_frame_text_and_query_graph_v1"
    assert structure_generation["raw"] == raw_generation


def test_build_structured_transcript_retries_invalid_query_graph(monkeypatch):
    job = SimpleNamespace(
        id="video-1",
        title="demo",
        filename="demo.mp4",
        source_bucket="bucket",
        source_object_key="videos/video-1/source/demo.mp4",
    )
    raw_response = {
        "sentences": [
            {"index": 0, "start_seconds": 0.0, "end_seconds": 1.0, "text": "Connect the arm to the base."},
        ],
    }
    calls: list[str] = []

    def fake_generate_semantic_tree(**kwargs):
        calls.append(str(kwargs.get("retry_feedback") or ""))
        if len(calls) == 1:
            return {
                "title": "demo",
                "sections": [
                    {
                        "title": "Connect arm",
                        "source_segment_indices": [0],
                        "polished_text": "Connect the arm to the base.",
                        "business_frame_text": "The arm is positioned close to the base for connection.",
                        "query_graph": {
                            "nodes": [
                                {"id": "arm", "label": "arm", "role": "part", "required": True, "weight": 0.5},
                            ],
                            "edges": [
                                {"from": "arm", "relation": "near", "to": "base", "required": True, "weight": 1.0},
                            ],
                        },
                    }
                ],
            }
        return {
            "title": "demo",
            "sections": [
                {
                    "title": "Connect arm",
                    "source_segment_indices": [0],
                    "polished_text": "Connect the arm to the base.",
                    "business_frame_text": "The arm is positioned close to the base for connection.",
                    "query_graph": {
                        "nodes": [
                            {"id": "arm", "label": "arm", "role": "part", "required": True, "weight": 0.5},
                            {"id": "base", "label": "base", "role": "part", "required": True, "weight": 0.5},
                        ],
                        "edges": [
                            {"from": "arm", "relation": "near", "to": "base", "required": True, "weight": 1.0},
                        ],
                    },
                }
            ],
        }

    monkeypatch.setattr(transcript_tree_module, "generate_semantic_tree", fake_generate_semantic_tree)

    result = build_structured_transcript(
        job=job,
        raw_response=raw_response,
        frames=[],
        duration_ms=1000,
    )

    section = result.tree["tree"]["sections"][0]
    assert len(calls) == 2
    assert calls[0] == ""
    assert "section.query_graph edge endpoint must reference an existing node id" in calls[1]
    assert [node["id"] for node in section["query_graph"]["nodes"]] == ["arm", "base"]
