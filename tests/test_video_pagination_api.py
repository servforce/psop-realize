from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import videos
from app.db.video import VideoBase
from app.models.video import VideoJob


STATES = ['uploaded', 'queued', 'processing', 'completed', 'completed_with_warnings', 'failed', 'deleting']


@pytest.fixture
def catalog_api(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    VideoBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(videos, 'VideoSessionLocal', factory)
    with factory() as session:
        session.add_all([
            VideoJob(id=f'job-{i:03}', title=f'机械臂 {"Demo" if i % 2 == 0 else "安装"} {i:03}',
                     filename=f'clip-{i:03}.mp4', status=STATES[i % len(STATES)],
                     created_at=datetime(2026, 9, 10, tzinfo=timezone.utc) + timedelta(seconds=i // 3))
            for i in range(65)
        ])
        session.commit()
    app = FastAPI()
    app.include_router(videos.router)
    with TestClient(app) as client:
        yield client, factory, engine
    engine.dispose()


def test_pages_cover_more_than_the_legacy_30_rows_without_duplicates(catalog_api):
    client, _, _ = catalog_api
    ids = []
    for page in range(1, 5):
        response = client.get('/api/videos', params={'page': page})
        assert response.status_code == 200
        data = response.json()
        assert (data['total'], data['page'], data['page_size'], data['total_pages']) == (65, page, 20, 4)
        ids.extend(job['id'] for job in data['items'])
    assert ids == [f'job-{i:03}' for i in reversed(range(65))]
    assert len(client.get('/api/videos').json()) == 30  # Legacy array contract is unchanged.


@pytest.mark.parametrize('state', STATES)
def test_search_and_status_are_applied_before_paging(catalog_api, state):
    client, _, _ = catalog_api
    expected = [f'job-{i:03}' for i in range(65) if i % 2 == 0 and STATES[i % len(STATES)] == state]
    data = client.get('/api/videos', params={'page': 1, 'page_size': 2, 'query': ' demo ', 'status': state, 'sort': 'asc'}).json()
    assert data['total'] == len(expected)
    assert [item['id'] for item in data['items']] == expected[:2]
    assert all(item['status'] == state for item in data['items'])


def test_filename_search_finds_records_outside_the_legacy_window(catalog_api):
    client, _, _ = catalog_api
    data = client.get('/api/videos', params={'page': 1, 'query': 'CLIP-000.MP4'}).json()
    assert data['total'] == 1
    assert data['items'][0]['id'] == 'job-000'


@pytest.mark.parametrize('query', ['%', '_', '\\', "' OR 1=1 --"])
def test_search_escapes_wildcards_and_uses_bound_parameters(catalog_api, query):
    client, factory, _ = catalog_api
    with factory() as session:
        session.add(VideoJob(id='literal', title=f'Literal {query}', filename='literal.mp4'))
        session.commit()
    data = client.get('/api/videos', params={'page': 1, 'query': query}).json()
    assert data['total'] == 1
    assert [item['id'] for item in data['items']] == ['literal']


def test_empty_results_and_out_of_range_pages_are_clamped(catalog_api):
    client, _, _ = catalog_api
    empty = client.get('/api/videos', params={'page': 99, 'query': '不存在的文件'}).json()
    assert empty == {'items': [], 'total': 0, 'page': 1, 'page_size': 20, 'total_pages': 1}
    last = client.get('/api/videos', params={'page': 999}).json()
    assert last['page'] == last['total_pages'] == 4
    assert len(last['items']) == 5


def test_deleting_the_last_page_returns_the_new_valid_last_page(catalog_api):
    client, factory, _ = catalog_api
    with factory() as session:
        session.execute(delete(VideoJob).where(VideoJob.id.in_([f'job-{i:03}' for i in range(5)])))
        session.commit()
    data = client.get('/api/videos', params={'page': 4}).json()
    assert (data['total'], data['page'], data['total_pages']) == (60, 3, 3)
    assert len(data['items']) == 20


@pytest.mark.parametrize('params', [
    {'page': 0}, {'page': -1}, {'page': 'invalid'}, {'page': 1, 'page_size': 0},
    {'page': 1, 'page_size': 101}, {'page': 1, 'page_size': '1.5'},
    {'page': 1, 'status': 'invalid'}, {'page': 1, 'sort': 'invalid'}, {'page': 1, 'query': 'x' * 201},
])
def test_invalid_pagination_parameters_are_rejected(catalog_api, params):
    client, _, _ = catalog_api
    assert client.get('/api/videos', params=params).status_code == 422


def test_database_limits_the_result_instead_of_fetching_all_rows(catalog_api):
    client, _, engine = catalog_api
    queries = []
    def capture(_conn, _cursor, statement, parameters, _context, _many):
        queries.append((statement, parameters))
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        response = client.get('/api/videos', params={'page': 3, 'page_size': 10})
        assert len(response.json()['items']) == 10
        selects = [(sql, params) for sql, params in queries if sql.lstrip().upper().startswith('SELECT')]
        assert len(selects) == 2
        assert 'count(' in selects[0][0].lower()
        assert 'LIMIT' in selects[1][0] and 'OFFSET' in selects[1][0]
        assert selects[1][1][-2:] == (10, 20)
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
