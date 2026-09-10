from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.frontend import mount_frontend


def test_frontend_history_routes_and_local_assets():
    app = FastAPI()
    mount_frontend(app, Path('static'))
    with TestClient(app) as client:
        for route in ['/', '/videos', '/videos/task-123', '/uploads', '/usage']:
            response = client.get(route)
            assert response.status_code == 200
            assert '<base href="/">' in response.text
            assert 'x-data="frame"' in response.text
        for route in ['/assets/js/init-alpine.js', '/assets/css/style.compiled.css', '/pages/videos.html']:
            assert client.get(route).status_code == 200
        for route in ['/api/missing', '/assets/missing.js', '/unknown', '/pages/missing.html']:
            assert client.get(route).status_code == 404
