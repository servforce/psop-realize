from types import SimpleNamespace

import pytest

import app.services.semantic_frames as semantic_frames_module

from app.services.semantic_frames import (
    bbox_touches_border,
    build_query_graph_matches,
    build_query_spec,
    compute_section_frame_similarities,
    frames_for_section,
    FrameFeature,
    graph_index_match_score,
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


def test_bbox_touches_border_requires_actual_image_boundary_contact():
    assert bbox_touches_border([1, 10, 90, 90], width=100, height=100) is False
    assert bbox_touches_border([0, 10, 90, 90], width=100, height=100) is True
    assert bbox_touches_border([10, 10, 100, 90], width=100, height=100) is True


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


def test_build_query_graph_matches_ranks_candidate_frames_for_section_graph():
    job = SimpleNamespace(id="video-1")
    frames = [make_frame(1, 1.0), make_frame(2, 2.0)]
    section = {
        "index": 3,
        "title": "check wiring",
        "business_frame_text": "terminal labels and connected cable clearly visible",
        "query_graph": make_query_graph(["terminal", "wire"]),
    }
    similarities = {
        (3, 1, 1): 0.5,
        (3, 1, 2): 0.9,
    }

    candidates_by_operation = {
        (3, 1): [SimpleNamespace(frame=frames[0]), SimpleNamespace(frame=frames[1])],
    }

    matches = build_query_graph_matches(
        job=job,
        section=section,
        candidates_by_operation=candidates_by_operation,
        similarities=similarities,
    )

    assert [match["query_index"] for match in matches] == [1]
    assert matches[0]["section_title"] == "check wiring"
    assert matches[0]["source"] == "section_query_graph"
    assert [frame["frame_id"] for frame in matches[0]["frames"]] == [2, 1]


def test_normalize_graph_index_accepts_local_object_payload():
    payload = {
        "backend": "finetuned_yolo_world_sam",
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

    assert normalized["backend"] == "finetuned_yolo_world_sam"
    assert normalized["objects"][0]["label"] == "servo"
    assert normalized["signals"]["clear_key_region"] is True
    assert normalized["signals"]["has_servo"] is True


def test_graph_index_match_score_prefers_matching_labels_and_signals():
    section = {"index": 1, "title": "servo base", "text": "servo base"}
    operation = {
        "index": 1,
        "operation_text": "servo base",
        "business_frame_text": "servo base",
        "query_graph": make_query_graph(["servo", "base"]),
        "priority": "high",
    }
    query_spec = build_query_spec(section=section, operation=operation)
    graph_index = {
        "backend": "finetuned_yolo_world_sam",
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


def test_graph_index_match_score_ignores_tiny_subject_for_overview_frames():
    section = {"index": 1, "title": "servo base", "text": "servo base"}
    operation = {
        "index": 1,
        "operation_text": "servo base",
        "business_frame_text": "servo base",
        "query_graph": make_query_graph(["servo", "base"]),
        "priority": "high",
    }
    query_spec = build_query_spec(section=section, operation=operation)
    graph_index = {
        "backend": "finetuned_yolo_world_sam",
        "image": {"size": {"width": 1000, "height": 700}},
        "objects": [
            {"label": "servo", "bbox": [120, 80, 180, 140], "confidence": 0.9, "bbox_area_ratio": 0.005},
            {"label": "base", "bbox": [210, 90, 270, 160], "confidence": 0.88, "bbox_area_ratio": 0.006},
        ],
        "relations": [{"from": "servo", "relation": "near", "to": "base", "score": 0.8}],
        "signals": {"tiny_subject": True, "sharp": True, "not_blurry": True, "detail_rich": True},
    }

    _, _, _, negative_hits = graph_index_match_score(
        query_spec=query_spec,
        graph_index=graph_index,
        quality_score_value=0.8,
    )

    assert "tiny_subject" not in negative_hits


def test_graph_index_match_score_rewards_spatial_proximity_and_relation():
    section = {"index": 1, "title": "servo base", "text": "servo base"}
    operation = {
        "index": 1,
        "operation_text": "servo base",
        "business_frame_text": "servo base",
        "query_graph": make_query_graph(["servo", "base"]),
        "priority": "high",
    }
    query_spec = build_query_spec(section=section, operation=operation)
    close_graph_index = {
        "backend": "finetuned_yolo_world_sam",
        "image": {"size": {"width": 400, "height": 300}},
        "objects": [
            {"label": "servo", "bbox": [20, 20, 120, 220], "confidence": 0.95, "mask_area_ratio": 0.12, "centroid_x": 70, "centroid_y": 120, "touches_border": False},
            {"label": "base", "bbox": [130, 30, 250, 220], "confidence": 0.93, "mask_area_ratio": 0.14, "centroid_x": 190, "centroid_y": 125, "touches_border": False},
        ],
        "relations": [{"from": "servo", "relation": "near", "to": "base", "score": 0.9}],
        "signals": {"clear_key_region": True, "close_up": True, "sharp": True, "not_blurry": True, "detail_rich": True},
    }
    far_graph_index = {
        "backend": "finetuned_yolo_world_sam",
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
        "business_frame_text": "servo base",
        "query_graph": make_query_graph(["servo", "base"]),
        "priority": "high",
    }
    query_spec = build_query_spec(section=section, operation=operation)
    clear_graph_index = {
        "backend": "finetuned_yolo_world_sam",
        "image": {"size": {"width": 400, "height": 300}},
        "objects": [
            {"label": "servo", "bbox": [20, 20, 120, 220], "confidence": 0.95, "mask_area_ratio": 0.12, "centroid_x": 70, "centroid_y": 120, "touches_border": False},
            {"label": "base", "bbox": [130, 30, 250, 220], "confidence": 0.93, "mask_area_ratio": 0.14, "centroid_x": 190, "centroid_y": 125, "touches_border": False},
        ],
        "relations": [{"from": "servo", "relation": "near", "to": "base", "score": 0.9}],
        "signals": {"clear_key_region": True, "close_up": True, "sharp": True, "not_blurry": True, "detail_rich": True},
    }
    occluded_graph_index = {
        "backend": "finetuned_yolo_world_sam",
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


def test_compute_section_frame_similarities_requires_query_graph():
    frame = SimpleNamespace(id=1, timestamp_ms=1000, timestamp_seconds=1.0)
    feature = FrameFeature(frame=frame, path=SimpleNamespace(), quality={}, quality_score=0.8)
    section = {
        "index": 1,
        "title": "servo",
        "business_frame_text": "servo clearly visible",
    }

    with pytest.raises(RuntimeError, match="query_graph"):
        compute_section_frame_similarities(
            sections=[section],
            candidates_by_operation={(1, 1): [feature]},
        )


def test_finetuned_match_details_use_single_video_matching_pipeline(monkeypatch):
    import app.services.finetuned_frame_matching as finetuned_frame_matching_module

    calls = []

    monkeypatch.setattr(
        finetuned_frame_matching_module,
        "segment_images_finetuned_detect",
        lambda image_files: calls.append([name for name, _ in image_files]) or [
            SimpleNamespace(
                filename="000001.jpg",
                objects=[{"label": "servo", "confidence": 0.95, "bbox": [0, 0, 50, 50], "mask_area_ratio": 0.2}],
                relations=[],
                quality={"width": 100, "height": 100, "blur_score": 100, "mean_brightness": 128},
                bbox_image_base64="bbox1",
                mask_image_base64="mask1",
                mask_available=True,
                mobile_sam_device="cpu",
                yolo_world_device="cpu",
            )
        ],
        raising=False,
    )

    frame = make_frame(1, 1.0)
    frame.object_key = "videos/video-1/frames/000001.jpg"
    feature = FrameFeature(
        frame=frame,
        path=SimpleNamespace(read_bytes=lambda: b"image"),
        quality={"width": 100, "height": 100, "blur_score": 100, "mean_brightness": 128},
        quality_score=0.8,
    )
    section = {
        "index": 1,
        "title": "servo",
        "business_frame_text": "servo clearly visible",
        "query_graph": make_query_graph(["servo"]),
    }

    details = semantic_frames_module.compute_section_frame_match_details_finetuned(
        sections=[section],
        candidates_by_operation={(1, 1): [feature]},
    )

    detail = details[(1, 1, 1)]
    assert calls == [["000001.jpg"]]
    assert detail.backend == "finetuned_yolo_world_sam"
    assert detail.mask_image_base64 == "mask1"
    assert detail.mask_available is True
    assert "servo" in detail.matched
