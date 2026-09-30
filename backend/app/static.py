"""Serve the built web UI with an SPA fallback; /api is never shadowed."""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles


def mount_ui(app: FastAPI, ui_dir: Path) -> None:
    assets = ui_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")
    index = ui_dir / "index.html"
    root = ui_dir.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str, request: Request) -> Response:
        if path.startswith("api/") or path == "api":
            raise HTTPException(status_code=404)
        candidate = (ui_dir / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
