"""Static frontend integration, kept independent of database/model startup."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def mount_frontend(app: FastAPI, root: Path | str = "static") -> None:
    root = Path(root).resolve()
    for directory in ("assets", "pages", "node_modules"):
        app.mount(f"/{directory}", StaticFiles(directory=root / directory, check_dir=False), name=f"frontend-{directory}")

    def index(video_id: str = ""):
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-store"})

    # Register only frontend routes, so missing API/resources still return 404.
    for route in ("/", "/videos", "/videos/{video_id}", "/uploads", "/usage"):
        app.add_api_route(route, index, methods=["GET"], include_in_schema=False)
