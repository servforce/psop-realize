"""The supported media surface and safe upgrade from retired database schema."""
import ast
import io
import json
from dataclasses import fields
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Boolean, Column, MetaData, String, Table, create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api import semantic_frames, usage, videos
from app.core.video_config import VideoSettings
from app.db.schema_cleanup import remove_retired_media_schema
from app.db.video import VideoBase
from app.models.video import VideoFrame, VideoJob, VideoUsageRecord
from app.services import frame_selection, video_parsing
from app.services.transcript_tree import attach_media_to_transcript_tree


def test_public_media_surface_has_only_supported_endpoints_and_modes():
    app = FastAPI()
    for router in [videos.router, semantic_frames.router, usage.router]:
        app.include_router(router)
    paths = app.openapi()['paths']
    assert not any('wireframe' in path for path in paths)
    assert '/api/semantic-frames/image' in paths
    for suffix in ['frames', 'semantic-frames', 'transcript', 'markdown', 'export', 'parse']:
        assert f'/api/videos/{{video_id}}/{suffix}' in paths
    with TestClient(app) as client:
        assert client.post('/api/videos/example/parse?mode=wireframes').status_code == 400
        assert client.get('/api/videos/example/wireframes').status_code == 404
        assert client.get('/api/videos/example/wireframes/jobs/latest').status_code == 404
        assert client.get('/api/videos/example/wireframes/jobs/job-id').status_code == 404
        assert client.post('/api/wireframes/image').status_code == 404


def test_active_models_and_response_payloads_have_no_retired_fields():
    assert set(VideoBase.metadata.tables) == {'video_jobs', 'video_frames', 'video_usage_records'}
    assert not any('wireframe' in column.name for column in VideoFrame.__table__.columns)
    job_payload = videos.job_to_dict(VideoJob(id='video-id', status='completed'))
    frame_payload = videos.frame_to_dict('video-id', VideoFrame(id=1, video_id='video-id', object_key='videos/video-id/frames/1.jpg', selection_status='selected'))
    assert frame_payload['selection_status'] == 'selected'
    assert not any('wireframe' in key for key in job_payload | frame_payload)
    names = {field.name for field in fields(VideoSettings)}
    assert not any('wireframe' in name or name.startswith('qwen_vl_') for name in names)
    assert 'video_frame_selection_min_blur_score' in names
    assert 'video_graph_index_finetuned_yolo_world_model' in names


def test_legacy_schema_cleanup_preserves_active_data_and_allows_new_frame_inserts():
    engine = create_engine('sqlite://')
    legacy = MetaData()
    for table in VideoBase.metadata.sorted_tables:
        table.to_metadata(legacy)
    old_frames = legacy.tables['video_frames']
    # This historical Python-side default never supplied a DB-side default.
    old_frames.append_column(Column('selected_for_wireframe', Boolean, nullable=False, default=True))
    Table('wireframe_jobs', legacy, Column('id', String(64), primary_key=True))
    legacy.create_all(engine)
    with engine.begin() as connection:
        connection.execute(old_frames.insert().values(video_id='existing', selection_status='selected', selection_score=.91))
        connection.execute(legacy.tables['wireframe_jobs'].insert().values(id='retired'))
    with Session(engine) as session:
        session.add(VideoJob(id='existing', status='completed'))
        session.add(VideoUsageRecord(id='history', video_id='existing', total_tokens=123))
        session.commit()
        session.add(VideoFrame(video_id='new-before-upgrade'))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    with engine.begin() as connection:
        remove_retired_media_schema(connection)
        remove_retired_media_schema(connection)
    assert 'wireframe_jobs' not in inspect(engine).get_table_names()
    assert 'selected_for_wireframe' not in {column['name'] for column in inspect(engine).get_columns('video_frames')}
    with Session(engine) as session:
        existing = session.scalar(select(VideoFrame).where(VideoFrame.video_id == 'existing'))
        assert existing.selection_status == 'selected'
        assert existing.selection_score == .91
        assert session.get(VideoJob, 'existing').status == 'completed'
        assert session.get(VideoUsageRecord, 'history').total_tokens == 123
        session.add(VideoFrame(video_id='new-after-upgrade'))
        session.commit()
        assert session.scalar(select(VideoFrame).where(VideoFrame.video_id == 'new-after-upgrade')) is not None
    engine.dispose()


def test_schema_cleanup_is_a_noop_on_fresh_schema():
    engine = create_engine('sqlite://')
    VideoBase.metadata.create_all(engine)
    with engine.begin() as connection:
        remove_retired_media_schema(connection)
        remove_retired_media_schema(connection)
    assert set(inspect(engine).get_table_names()) == set(VideoBase.metadata.tables)
    engine.dispose()


def test_full_pipeline_remains_transcript_then_business_frames_then_markdown(monkeypatch):
    calls = []
    for name in ['transcript', 'keyframes', 'markdown']:
        monkeypatch.setattr(video_parsing, f'parse_{name}', lambda video_id, finalize, name=name: calls.append((name, video_id, finalize)))
    video_parsing.parse_full('video-id')
    assert calls == [('transcript', 'video-id', False), ('keyframes', 'video-id', False), ('markdown', 'video-id', True)]


def test_business_frame_quality_and_selection_bookkeeping_are_retained(monkeypatch):
    def unavailable(_):
        raise ImportError('OpenCV not loaded for this test')
    monkeypatch.setattr(frame_selection, 'analyze_quality_with_opencv', unavailable)
    buffer = io.BytesIO()
    Image.new('RGB', (32, 32), 'black').save(buffer, format='PNG')
    quality = frame_selection.analyze_image_quality(buffer.getvalue())
    assert not quality.passed
    assert 'too_dark_or_black' in quality.details['failures']
    frame = VideoFrame(id=1, video_id='video-id')
    frame_selection.mark_frame_selected(frame, score=1.2, reason='matched', details={'graph': True}, matched_section_index=2)
    assert frame.selection_status == 'selected'
    assert frame.selection_score == 1
    assert json.loads(frame.selection_details_json) == {'graph': True}
    frame_selection.mark_frame_rejected(frame, score=-1, reason='blur', details={}, matched_section_index=None)
    assert frame.selection_status == 'rejected'
    assert frame.selection_score == 0


def test_transcript_media_attachment_keeps_frame_timing_urls_and_query_graph():
    tree = {'tree': {'sections': [{'start_seconds': 1, 'end_seconds': 3, 'query_graph': {'nodes': []}}]}}
    frames = [VideoFrame(id=index, video_id='v', timestamp_seconds=index, timestamp_ms=index * 1000, object_key=f'videos/v/frames/{index}.jpg') for index in [0, 2, 4]]
    result = attach_media_to_transcript_tree(tree=tree, video_id='v', frames=frames)
    section = result['tree']['sections'][0]
    assert section['query_graph'] == {'nodes': []}
    assert [frame['id'] for frame in section['frames']] == [2]
    assert section['frames'][0]['url'] == '/api/videos/v/frames/2.jpg'
    assert not any('wireframe' in key for key in section | section['frames'][0])


def test_retired_identifiers_are_limited_to_schema_cleanup_and_absence_tests():
    roots = [Path('app'), Path('static/pages'), Path('static/assets/js'), Path('tools')]
    for root in roots:
        for path in root.rglob('*'):
            if not path.is_file() or path.suffix not in {'.py', '.js', '.html', '.md'} or path.name == 'schema_cleanup.py':
                continue
            source = path.read_text(encoding='utf-8-sig').lower()
            assert 'wireframe' not in source and '线框图' not in source, path
    assert not Path('tools/qwen-image-wireframe').exists()
    functions = [node.name for node in ast.parse(Path('app/services/video_parsing.py').read_text()).body if isinstance(node, ast.FunctionDef)]
    assert functions.count('parse_transcript') == 1
