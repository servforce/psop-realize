import importlib.util
import sys
import types
from types import SimpleNamespace


if importlib.util.find_spec("httpx") is None:
    sys.modules["httpx"] = types.ModuleType("httpx")

config = types.ModuleType("app.core.config")
config.settings = SimpleNamespace()
sys.modules["app.core.config"] = config

entities = types.ModuleType("app.models.entities")
entities.VideoFrame = object
entities.VideoJob = object
sys.modules["app.models.entities"] = entities

frame_selection = types.ModuleType("app.services.frame_selection")
frame_selection.analyze_image_quality = lambda image_bytes: None
frame_selection.mark_frame_rejected = lambda *args, **kwargs: None
frame_selection.mark_frame_selected = lambda *args, **kwargs: None
sys.modules["app.services.frame_selection"] = frame_selection

audit = types.ModuleType("app.services.audit")
audit.finish_call = lambda *args, **kwargs: None


class DummyLoggedCall:
    def __enter__(self):
        return None, "call-1"

    def __exit__(self, exc_type, exc, tb):
        return False


audit.logged_call_with_session = lambda *args, **kwargs: DummyLoggedCall()
sys.modules["app.services.audit"] = audit

from app.services.semantic_frames import (
    build_frame_query_matches,
    frames_for_section,
    normalize_frame_queries,
    normalize_frame_queries_source,
    section_time_window,
)


def make_frame(frame_id: int, seconds: float) -> SimpleNamespace:
    return SimpleNamespace(
        id=frame_id,
        video_id="video-1",
        timestamp_ms=int(seconds * 1000),
        timestamp_seconds=seconds,
        object_key=f"videos/video-1/frames/{frame_id:06d}.jpg",
        selection_status=None,
        selection_score=None,
        matched_section_index=None,
    )


def test_section_time_window_adds_padding_without_negative_start():
    section = {"start_seconds": 1.0, "end_seconds": 5.0}

    assert section_time_window(section, padding_seconds=2.0) == (0.0, 7.0)


def test_frames_for_section_uses_padded_section_window():
    frames = [
        make_frame(1, 7.9),
        make_frame(2, 8.0),
        make_frame(3, 10.0),
        make_frame(4, 14.0),
        make_frame(5, 16.0),
        make_frame(6, 16.1),
    ]
    section = {"start_seconds": 10.0, "end_seconds": 14.0}

    strict_ids = [frame.id for frame in frames_for_section(frames, section)]
    padded_ids = [frame.id for frame in frames_for_section(frames, section, padding_seconds=2.0)]

    assert strict_ids == [3, 4]
    assert padded_ids == [2, 3, 4, 5]


def test_normalize_frame_queries_limits_and_deduplicates_items():
    queries = normalize_frame_queries(
        ["check wiring", "measure voltage", "check wiring", "observe indicator"],
        title="section title",
        text="section text",
    )

    assert queries == ["check wiring", "measure voltage", "observe indicator"]


def test_normalize_frame_queries_source_detects_fallback_title_text():
    assert (
        normalize_frame_queries_source(
            None,
            raw_frame_queries=None,
            frame_queries=["title\nsection text"],
            title="title",
            text="section text",
        )
        == "fallback_title_text"
    )
    assert (
        normalize_frame_queries_source(
            None,
            raw_frame_queries=["model generated query"],
            frame_queries=["model generated query"],
            title="title",
            text="section text",
        )
        == "model"
    )


def test_build_frame_query_matches_ranks_all_candidate_frames_per_query():
    job = SimpleNamespace(id="video-1")
    frames = [make_frame(1, 1.0), make_frame(2, 2.0)]
    section = {
        "index": 3,
        "visual_operations": [
            {"index": 1, "operation_text": "check wiring", "frame_query": "check wiring"},
            {"index": 2, "operation_text": "measure voltage", "frame_query": "measure voltage"},
        ],
    }
    similarities = {
        (3, 1, 1): 0.5,
        (3, 1, 2): 0.9,
        (3, 2, 1): 0.8,
        (3, 2, 2): 0.4,
    }

    candidates_by_operation = {
        (3, 1): [SimpleNamespace(frame=frames[0]), SimpleNamespace(frame=frames[1])],
        (3, 2): [SimpleNamespace(frame=frames[0]), SimpleNamespace(frame=frames[1])],
    }

    matches = build_frame_query_matches(
        job=job,
        section=section,
        candidates_by_operation=candidates_by_operation,
        similarities=similarities,
    )

    assert [match["query_index"] for match in matches] == [1, 2]
    assert [match["operation_text"] for match in matches] == ["check wiring", "measure voltage"]
    assert [match["frame_queries_source"] for match in matches] == ["visual_operations", "visual_operations"]
    assert [frame["frame_id"] for frame in matches[0]["frames"]] == [2, 1]
    assert [frame["frame_id"] for frame in matches[1]["frames"]] == [1, 2]
