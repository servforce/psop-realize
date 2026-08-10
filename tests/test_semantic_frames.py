from types import SimpleNamespace

import pytest

import app.services.semantic_frames as semantic_frames_module

from app.services.semantic_frames import (
    build_frame_query_matches,
    build_query_spec,
    compute_section_frame_similarities,
    frames_for_section,
    FrameFeature,
    GraphIndexClient,
    graph_index_match_score,
    normalize_frame_queries,
    normalize_frame_queries_source,
    normalize_graph_index,
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


def make_query_graph(labels: list[str]) -> dict:
    nodes = [
        {
            "id": f"node_{index + 1}",
            "label": label,
            "role": "part",
            "required": True,
            "weight": 1.0 / max(1, len(labels)),
        }
        for index, label in enumerate(labels)
    ]
    edges = []
    if len(nodes) >= 2:
        edges.append(
            {
                "from": nodes[0]["id"],
                "relation": "near",
                "to": nodes[1]["id"],
                "required": False,
                "weight": 1.0,
            }
        )
    return {"nodes": nodes, "edges": edges}


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


def test_normalize_graph_index_accepts_local_object_payload():
    payload = {
        "backend": "yolo_world",
        "objects": [
            {
                "id": "det_1",
                "label": "servo",
                "bbox": [10, 20, 110, 220],
                "confidence": 0.91,
                "mask_area_ratio": 0.14,
                "bbox_area_ratio": 0.11,
                "centroid_x": 64.0,
                "centroid_y": 96.0,
                "touches_border": False,
            }
        ],
        "signals": {"clear_key_region": 1, "detail_rich": True},
    }

    normalized = normalize_graph_index(payload, prompt_terms=["servo", "base"])

    assert normalized["backend"] == "yolo_world"
    assert normalized["objects"][0]["label"] == "servo"
    assert normalized["signals"]["clear_key_region"] is True
    assert normalized["signals"]["has_servo"] is True


def test_graph_index_match_score_prefers_matching_labels_and_signals():
    section = {"index": 1, "title": "servo base", "text": "servo base"}
    operation = {
        "index": 1,
        "operation_text": "servo base",
        "frame_query": "servo base",
        "visual_terms": ["servo", "base"],
        "query_graph": make_query_graph(["servo", "base"]),
        "priority": "high",
    }
    query_spec = build_query_spec(section=section, operation=operation)
    graph_index = {
        "backend": "yolo_world",
        "objects": [
            {"label": "servo", "bbox": [12, 18, 108, 220], "confidence": 0.9},
            {"label": "base", "bbox": [140, 40, 300, 250], "confidence": 0.88},
        ],
        "relations": [{"from": "servo", "relation": "near", "to": "base", "score": 0.8}],
        "signals": {"clear_key_region": True, "close_up": True, "sharp": True, "not_blurry": True, "detail_rich": True},
    }

    score, matched, missing, negative_hits = graph_index_match_score(
        query_spec=query_spec,
        graph_index=graph_index,
        quality_score_value=0.8,
    )

    assert score > 0.5
    assert "servo" in matched
    assert "base" in matched
    assert missing == []
    assert negative_hits == []


def test_graph_index_match_score_rewards_spatial_proximity_and_relation():
    section = {"index": 1, "title": "servo base", "text": "servo base"}
    operation = {
        "index": 1,
        "operation_text": "servo base",
        "frame_query": "servo base",
        "visual_terms": ["servo", "base"],
        "query_graph": make_query_graph(["servo", "base"]),
        "priority": "high",
    }
    query_spec = build_query_spec(section=section, operation=operation)
    close_graph_index = {
        "backend": "yoloe_seg",
        "image": {"size": {"width": 400, "height": 300}},
        "objects": [
            {"label": "servo", "bbox": [20, 20, 120, 220], "confidence": 0.95, "mask_area_ratio": 0.12, "centroid_x": 70, "centroid_y": 120, "touches_border": False},
            {"label": "base", "bbox": [130, 30, 250, 220], "confidence": 0.93, "mask_area_ratio": 0.14, "centroid_x": 190, "centroid_y": 125, "touches_border": False},
        ],
        "relations": [{"from": "servo", "relation": "near", "to": "base", "score": 0.9}],
        "signals": {"clear_key_region": True, "close_up": True, "sharp": True, "not_blurry": True, "detail_rich": True},
    }
    far_graph_index = {
        "backend": "yoloe_seg",
        "image": {"size": {"width": 400, "height": 300}},
        "objects": [
            {"label": "servo", "bbox": [20, 20, 120, 220], "confidence": 0.95, "mask_area_ratio": 0.12, "centroid_x": 70, "centroid_y": 120, "touches_border": False},
            {"label": "base", "bbox": [290, 35, 390, 230], "confidence": 0.93, "mask_area_ratio": 0.14, "centroid_x": 340, "centroid_y": 132, "touches_border": False},
        ],
        "relations": [],
        "signals": {"clear_key_region": True, "close_up": True, "sharp": True, "not_blurry": True, "detail_rich": True},
    }

    close_score, _, _, _ = graph_index_match_score(
        query_spec=query_spec,
        graph_index=close_graph_index,
        quality_score_value=0.8,
    )
    far_score, _, _, _ = graph_index_match_score(
        query_spec=query_spec,
        graph_index=far_graph_index,
        quality_score_value=0.8,
    )

    assert close_score > far_score


def test_graph_index_match_score_penalizes_hand_occlusion():
    section = {"index": 1, "title": "servo base", "text": "servo base"}
    operation = {
        "index": 1,
        "operation_text": "servo base",
        "frame_query": "servo base",
        "visual_terms": ["servo", "base"],
        "query_graph": make_query_graph(["servo", "base"]),
        "priority": "high",
    }
    query_spec = build_query_spec(section=section, operation=operation)
    clear_graph_index = {
        "backend": "yoloe_seg",
        "image": {"size": {"width": 400, "height": 300}},
        "objects": [
            {"label": "servo", "bbox": [20, 20, 120, 220], "confidence": 0.95, "mask_area_ratio": 0.12, "centroid_x": 70, "centroid_y": 120, "touches_border": False},
            {"label": "base", "bbox": [130, 30, 250, 220], "confidence": 0.93, "mask_area_ratio": 0.14, "centroid_x": 190, "centroid_y": 125, "touches_border": False},
        ],
        "relations": [{"from": "servo", "relation": "near", "to": "base", "score": 0.9}],
        "signals": {"clear_key_region": True, "close_up": True, "sharp": True, "not_blurry": True, "detail_rich": True},
    }
    occluded_graph_index = {
        "backend": "yoloe_seg",
        "image": {"size": {"width": 400, "height": 300}},
        "objects": [
            {"label": "servo", "bbox": [20, 20, 120, 220], "confidence": 0.95, "mask_area_ratio": 0.12, "centroid_x": 70, "centroid_y": 120, "touches_border": False},
            {"label": "base", "bbox": [130, 30, 250, 220], "confidence": 0.93, "mask_area_ratio": 0.14, "centroid_x": 190, "centroid_y": 125, "touches_border": False},
            {"label": "hand", "bbox": [110, 50, 220, 210], "confidence": 0.92, "mask_area_ratio": 0.16, "centroid_x": 165, "centroid_y": 130, "touches_border": False},
        ],
        "relations": [{"from": "hand", "relation": "occludes", "to": "base", "score": 0.9}],
        "signals": {"clear_key_region": False, "close_up": True, "sharp": True, "not_blurry": True, "detail_rich": True, "occluded": True},
    }

    clear_score, _, _, clear_negatives = graph_index_match_score(
        query_spec=query_spec,
        graph_index=clear_graph_index,
        quality_score_value=0.8,
    )
    occluded_score, _, _, occluded_negatives = graph_index_match_score(
        query_spec=query_spec,
        graph_index=occluded_graph_index,
        quality_score_value=0.8,
    )

    assert occluded_score < clear_score
    assert "occluded" in occluded_negatives
    assert "occluded" not in clear_negatives


def test_compute_section_frame_similarities_uses_operation_visual_terms(monkeypatch):
    calls = []

    class FakeClient:
        def build_index(self, *, frame, prompt_terms=None):
            calls.append(list(prompt_terms or []))
            label = prompt_terms[0] if prompt_terms else "fallback"
            return {
                "backend": "yolo_world",
                "objects": [{"label": label, "confidence": 0.95}],
                "relations": [],
                "signals": {"clear_key_region": True, "close_up": True, "not_blurry": True},
            }

    monkeypatch.setattr(semantic_frames_module.GraphIndexClient, "from_settings", classmethod(lambda cls: FakeClient()))

    frame = SimpleNamespace(id=1, timestamp_ms=1000, timestamp_seconds=1.0)
    feature = FrameFeature(frame=frame, path=SimpleNamespace(), quality={}, quality_score=0.8)
    section = {
        "index": 1,
        "visual_operations": [
            {
                "index": 1,
                "operation_text": "servo base",
                "frame_query": "servo base",
                "visual_terms": ["servo", "base"],
                "query_graph": make_query_graph(["servo", "base"]),
            }
        ],
    }

    similarities = compute_section_frame_similarities(
        sections=[section],
        candidates_by_operation={(1, 1): [feature]},
    )

    assert calls and "servo" in calls[0] and "base" in calls[0]
    assert similarities[(1, 1, 1)] > 0.0


def test_compute_section_frame_similarities_keeps_prompt_terms_operation_scoped(monkeypatch):
    calls = []

    class FakeClient:
        def build_index(self, *, frame, prompt_terms=None):
            calls.append((int(frame.frame.id), list(prompt_terms or [])))
            return {
                "backend": "yoloe_seg",
                "objects": [{"label": term, "confidence": 0.95} for term in (prompt_terms or [])],
                "relations": [],
                "signals": {"clear_key_region": True, "close_up": True, "not_blurry": True},
            }

    monkeypatch.setattr(semantic_frames_module.GraphIndexClient, "from_settings", classmethod(lambda cls: FakeClient()))

    frame = SimpleNamespace(id=1, timestamp_ms=1000, timestamp_seconds=1.0)
    feature = FrameFeature(frame=frame, path=SimpleNamespace(), quality={}, quality_score=0.8)
    section = {
        "index": 1,
        "visual_operations": [
            {
                "index": 1,
                "operation_text": "servo",
                "frame_query": "servo",
                "visual_terms": ["servo"],
                "query_graph": make_query_graph(["servo"]),
            },
            {
                "index": 2,
                "operation_text": "base",
                "frame_query": "base",
                "visual_terms": ["base"],
                "query_graph": make_query_graph(["base"]),
            },
        ],
    }

    similarities = compute_section_frame_similarities(
        sections=[section],
        candidates_by_operation={(1, 1): [feature], (1, 2): [feature]},
    )

    assert calls == [(1, ["servo"]), (1, ["base"])]
    assert similarities[(1, 1, 1)] > 0.0
    assert similarities[(1, 2, 1)] > 0.0


def test_compute_section_frame_similarities_uses_query_graph_when_visual_terms_missing(monkeypatch):
    calls = []

    class FakeClient:
        def build_index(self, *, frame, prompt_terms=None):
            calls.append(list(prompt_terms or []))
            return {
                "backend": "yoloe_seg",
                "objects": [{"label": "servo", "confidence": 0.95}],
                "relations": [],
                "signals": {"clear_key_region": True, "close_up": True, "not_blurry": True},
            }

    monkeypatch.setattr(semantic_frames_module.GraphIndexClient, "from_settings", classmethod(lambda cls: FakeClient()))

    frame = SimpleNamespace(id=1, timestamp_ms=1000, timestamp_seconds=1.0)
    feature = FrameFeature(frame=frame, path=SimpleNamespace(), quality={}, quality_score=0.8)
    section = {
        "index": 1,
        "visual_operations": [
            {
                "index": 1,
                "operation_text": "servo",
                "frame_query": "servo",
                "query_graph": make_query_graph(["servo"]),
            }
        ],
    }

    similarities = compute_section_frame_similarities(
        sections=[section],
        candidates_by_operation={(1, 1): [feature]},
    )

    assert calls == [["servo"]]
    assert similarities[(1, 1, 1)] > 0.0


def test_compute_section_frame_similarities_requires_query_graph(monkeypatch):
    calls = []

    class FakeClient:
        def build_index(self, *, frame, prompt_terms=None):
            calls.append(list(prompt_terms or []))
            return {}

    monkeypatch.setattr(semantic_frames_module.GraphIndexClient, "from_settings", classmethod(lambda cls: FakeClient()))

    frame = SimpleNamespace(id=1, timestamp_ms=1000, timestamp_seconds=1.0)
    feature = FrameFeature(frame=frame, path=SimpleNamespace(), quality={}, quality_score=0.8)
    section = {
        "index": 1,
        "visual_operations": [
            {
                "index": 1,
                "operation_text": "servo",
                "frame_query": "servo",
            }
        ],
    }

    with pytest.raises(RuntimeError, match="query_graph"):
        compute_section_frame_similarities(
            sections=[section],
            candidates_by_operation={(1, 1): [feature]},
        )

    assert calls == []


def test_graph_index_client_sets_prompt_classes_once_for_reused_yoloe_prompt(monkeypatch):
    class FakeModel:
        def __init__(self):
            self.class_calls = []

        def set_classes(self, classes):
            self.class_calls.append(list(classes))

        def predict(self, **kwargs):
            return []

    fake_model = FakeModel()
    monkeypatch.setattr(semantic_frames_module, "load_yoloe_model", lambda model_name: fake_model)
    monkeypatch.setattr(
        semantic_frames_module,
        "load_image_metadata",
        lambda image_path: {"size": {"width": 100, "height": 100}, "mode": "RGB", "area": 10000},
    )

    client = GraphIndexClient(
        backend="yoloe_seg",
        device="cpu",
        yolo_world_model="yolov8s-world.pt",
        yoloe_model="yoloe-26n-seg.pt",
        yolo_world_confidence=0.18,
        yolo_world_iou=0.35,
        yolo_world_max_det=12,
    )
    first = FrameFeature(frame=SimpleNamespace(id=1), path=SimpleNamespace(), quality={}, quality_score=0.8)
    second = FrameFeature(frame=SimpleNamespace(id=2), path=SimpleNamespace(), quality={}, quality_score=0.8)

    client.build_index(frame=first, prompt_terms=["servo", "hand"])
    client.build_index(frame=second, prompt_terms=["servo", "hand"])

    assert fake_model.class_calls == [["servo", "hand"]]


def test_graph_index_client_adds_hand_as_yoloe_auxiliary_prompt(monkeypatch):
    class FakeModel:
        def __init__(self):
            self.class_calls = []

        def set_classes(self, classes):
            self.class_calls.append(list(classes))

        def predict(self, **kwargs):
            return []

    fake_model = FakeModel()
    monkeypatch.setattr(semantic_frames_module, "load_yoloe_model", lambda model_name: fake_model)
    monkeypatch.setattr(
        semantic_frames_module,
        "load_image_metadata",
        lambda image_path: {"size": {"width": 100, "height": 100}, "mode": "RGB", "area": 10000},
    )

    client = GraphIndexClient(
        backend="yoloe_seg",
        device="cpu",
        yolo_world_model="yolov8s-world.pt",
        yoloe_model="yoloe-26n-seg.pt",
        yolo_world_confidence=0.18,
        yolo_world_iou=0.35,
        yolo_world_max_det=12,
    )
    frame = FrameFeature(frame=SimpleNamespace(id=1), path=SimpleNamespace(), quality={}, quality_score=0.8)

    graph_index = client.build_index(frame=frame, prompt_terms=["servo"])

    assert fake_model.class_calls == [["servo", "hand"]]
    assert graph_index["prompt_terms"] == ["servo", "hand"]
