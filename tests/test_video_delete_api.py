from dataclasses import replace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import videos
from app.db.video import VideoBase
from app.models.video import VideoFrame, VideoJob, VideoUsageRecord
from app.services.storage import StorageService


@pytest.fixture
def api(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    VideoBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(videos, 'VideoSessionLocal', factory)
    monkeypatch.setattr(videos, 'settings', replace(videos.settings, object_store_bucket='default-bucket'))
    cleanup = Mock()
    monkeypatch.setattr(videos.storage_service, 'delete_video_objects', cleanup)
    with factory() as session:
        session.add_all([
            VideoJob(id='target', title='Target', status='completed', source_bucket='source-bucket', analysis_video_bucket='proxy-bucket'),
            VideoJob(id='unrelated', status='completed', source_bucket='source-bucket'),
            VideoFrame(video_id='target', bucket='frames-bucket'),
            VideoFrame(video_id='unrelated', bucket='frames-bucket'),
            VideoUsageRecord(id='usage-target', video_id='target', status='completed', total_tokens=123),
        ])
        session.commit()
    app = FastAPI()
    app.include_router(videos.router)
    with TestClient(app) as client:
        yield client, factory, cleanup
    engine.dispose()


def test_delete_cleans_objects_and_related_rows_but_keeps_usage_and_other_videos(api):
    client, factory, cleanup = api
    response = client.delete('/api/videos/target')
    assert response.status_code == 200
    assert response.json() == {'id': 'target', 'deleted': True}
    assert {call.kwargs['bucket'] for call in cleanup.call_args_list} == {'default-bucket', 'source-bucket', 'proxy-bucket', 'frames-bucket'}
    assert all(call.kwargs['video_id'] == 'target' for call in cleanup.call_args_list)
    with factory() as session:
        assert session.get(VideoJob, 'target') is None
        assert session.scalar(select(VideoFrame).where(VideoFrame.video_id == 'target')) is None
        assert session.get(VideoUsageRecord, 'usage-target').total_tokens == 123
        assert session.get(VideoJob, 'unrelated') is not None
        assert session.scalar(select(VideoFrame).where(VideoFrame.video_id == 'unrelated')) is not None
    assert client.get('/api/videos/target').status_code == 404
    assert [job['id'] for job in client.get('/api/videos').json()] == ['unrelated']
    assert client.delete('/api/videos/target').status_code == 404


@pytest.mark.parametrize('status', ['queued', 'processing', 'uploading', 'unknown'])
def test_delete_rejects_active_or_unknown_states_before_touching_storage(api, status):
    client, factory, cleanup = api
    with factory() as session:
        session.get(VideoJob, 'target').status = status
        session.commit()
    assert client.delete('/api/videos/target').status_code == 409
    cleanup.assert_not_called()
    with factory() as session:
        assert session.get(VideoJob, 'target').status == status




def test_partial_cleanup_failure_is_retryable_and_blocks_reparse(api):
    client, factory, cleanup = api
    cleanup.side_effect = [None, RuntimeError('private storage credentials')]
    response = client.delete('/api/videos/target')
    assert response.status_code == 503
    assert 'private' not in response.text
    with factory() as session:
        assert session.get(VideoJob, 'target').status == 'deleting'
        assert session.scalar(select(VideoFrame).where(VideoFrame.video_id == 'target')) is not None
    for mode in ['full', 'transcript', 'keyframes', 'markdown']:
        assert client.post(f'/api/videos/target/parse?mode={mode}').status_code == 409
    cleanup.side_effect = None
    assert client.delete('/api/videos/target').status_code == 200


def test_internal_storage_key_error_is_not_mistaken_for_an_already_deleted_video(api):
    client, factory, cleanup = api
    cleanup.side_effect = KeyError('internal response')
    assert client.delete('/api/videos/target').status_code == 503
    with factory() as session:
        assert session.get(VideoJob, 'target') is not None


def test_missing_or_malformed_ids_do_not_touch_storage(api):
    client, _, cleanup = api
    assert client.delete('/api/videos/missing').status_code == 404
    assert client.delete('/api/videos/invalid..id').status_code == 400
    cleanup.assert_not_called()


@pytest.mark.parametrize('status', ['uploaded', 'failed', 'completed_with_warnings', 'deleting'])
def test_delete_accepts_idle_terminal_and_retry_states(api, status):
    client, factory, _ = api
    with factory() as session:
        session.get(VideoJob, 'target').status = status
        session.commit()
    assert client.delete('/api/videos/target').status_code == 200


def test_storage_cleanup_is_paginated_batched_and_scoped_to_exact_prefix():
    storage = StorageService()
    client = Mock()
    storage._client = client
    client.get_paginator.return_value.paginate.return_value = [
        {'Contents': [{'Key': f'videos/target/frames/{i}.jpg'} for i in range(1001)]},
        {'Contents': [{'Key': 'videos/target/source/video.mp4'}]}, {},
    ]
    client.delete_objects.return_value = {}
    storage.delete_video_objects(bucket='test-bucket', video_id='target')
    client.get_paginator.return_value.paginate.assert_called_once_with(Bucket='test-bucket', Prefix='videos/target/')
    assert [len(call.kwargs['Delete']['Objects']) for call in client.delete_objects.call_args_list] == [1000, 1, 1]


def test_storage_partial_errors_are_not_reported_as_success():
    storage = StorageService()
    storage._client = Mock()
    storage._client.get_paginator.return_value.paginate.return_value = [{'Contents': [{'Key': 'videos/target/a'}]}]
    storage._client.delete_objects.return_value = {'Errors': [{'Code': 'AccessDenied'}]}
    with pytest.raises(RuntimeError, match='incomplete'):
        storage.delete_video_objects(bucket='test-bucket', video_id='target')


@pytest.mark.parametrize('video_id', ['', '..', '../other', 'target/', 'a' * 65])
def test_storage_rejects_unsafe_namespace(video_id):
    storage = StorageService()
    storage._client = Mock()
    with pytest.raises(ValueError):
        storage.delete_video_objects(bucket='test-bucket', video_id=video_id)
    storage._client.get_paginator.assert_not_called()


def test_storage_refuses_sibling_objects_in_unexpected_listing():
    storage = StorageService()
    storage._client = Mock()
    storage._client.get_paginator.return_value.paginate.return_value = [{'Contents': [{'Key': 'videos/target-other/a'}]}]
    with pytest.raises(ValueError):
        storage.delete_video_objects(bucket='test-bucket', video_id='target')
    storage._client.delete_objects.assert_not_called()
