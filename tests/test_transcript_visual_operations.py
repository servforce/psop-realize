import importlib.util
import sys
import types
from types import SimpleNamespace


if importlib.util.find_spec("httpx") is None:
    sys.modules["httpx"] = types.ModuleType("httpx")

from app.core.config import settings
from app.services.transcript_tree import build_transcript_raw_cache_info, build_transcript_structure_cache_info, normalize_transcript_tree

if not hasattr(settings, "video_max_visual_operations_per_section"):
    settings.video_max_visual_operations_per_section = 3
if not hasattr(settings, "transcript_structure_model"):
    settings.transcript_structure_model = "test-model"
if not hasattr(settings, "local_asr_model_label"):
    settings.local_asr_model_label = "test-asr"


def test_normalize_transcript_tree_adds_visual_operation_time_ranges_from_asr_sentences():
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
                "text": "Open the cabinet, check the labels, and connect the cable.",
                "visual_operations": [
                    {
                        "operation_text": "Check the terminal labels",
                        "source_segment_indices": [1],
                        "priority": "high",
                        "frame_query": "terminal labels and wiring positions clearly visible",
                        "visual_terms": ["terminal label", "wire"],
                        "query_graph": {
                            "nodes": [
                                {"id": "terminal", "label": "terminal label", "role": "part", "required": True, "weight": 0.6},
                                {"id": "wire", "label": "wire", "role": "part", "required": True, "weight": 0.4},
                            ],
                            "edges": [
                                {"from": "terminal", "relation": "near", "to": "wire", "required": False, "weight": 1.0},
                            ],
                        },
                    },
                    {
                        "operation_text": "Connect the cable",
                        "source_segment_indices": [2],
                        "priority": "medium",
                        "frame_query": "cable connected to the correct terminal clearly visible",
                        "query_graph": {
                            "nodes": [
                                {"id": "cable", "label": "cable", "role": "part", "required": True, "weight": 0.5},
                                {"id": "terminal", "label": "terminal", "role": "part", "required": True, "weight": 0.5},
                            ],
                            "edges": [
                                {"from": "cable", "relation": "connected_to", "to": "terminal", "required": True, "weight": 1.0},
                            ],
                        },
                    },
                ],
            }
        ],
    }

    normalized = normalize_transcript_tree(
        tree=tree,
        job=job,
        source_segments=source_segments,
        frames=[],
        wireframes=[],
        duration_ms=5000,
    )

    operations = normalized["tree"]["sections"][0]["visual_operations"]
    assert [operation["source_segment_indices"] for operation in operations] == [[1], [2]]
    assert [(operation["start_seconds"], operation["end_seconds"]) for operation in operations] == [(1.0, 3.0), (3.0, 5.0)]
    assert operations[0]["visual_terms"] == ["terminal label", "wire"]
    assert operations[0]["query_graph"]["nodes"][0]["id"] == "terminal"
    assert operations[1]["visual_terms"] == ["cable", "terminal"]
    assert normalized["tree"]["sections"][0]["frame_queries"] == [
        "terminal labels and wiring positions clearly visible",
        "cable connected to the correct terminal clearly visible",
    ]


def test_transcript_cache_info_is_split_into_raw_and_structure_layers():
    job = SimpleNamespace(
        id="video-1",
        title="demo",
        filename="demo.mp4",
        source_bucket="bucket",
        source_object_key="videos/video-1/source/demo.mp4",
    )

    raw_cache = build_transcript_raw_cache_info(job=job)
    structure_cache = build_transcript_structure_cache_info(job=job)

    assert raw_cache["kind"] == "transcript_raw"
    assert raw_cache["source_object_key"] == job.source_object_key
    assert structure_cache["kind"] == "transcript_tree"
    assert structure_cache["raw"] == raw_cache
