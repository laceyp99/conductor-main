import asyncio
import sys
from importlib.metadata import version
from pathlib import Path

import gradio as gr
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from conductor_main import app


def test_gradio_meets_windows_path_traversal_security_floor():
    resolved_version = tuple(int(part) for part in version("gradio").split(".")[:3])

    assert resolved_version >= (6, 7, 0)


@pytest.mark.skipif(
    sys.platform != "win32" or sys.version_info < (3, 13),
    reason="GHSA-39mp-8hj3-5c49 affects Windows on Python 3.13+",
)
def test_root_relative_static_path_cannot_read_windows_files(monkeypatch):
    monkeypatch.setattr(app.ollama_api, "get_ollama_status", lambda: {"available": False})
    monkeypatch.setattr(app, "load_history", lambda: [])

    demo = app.create_demo(playback_status=(False, "disabled for security test"))
    server = gr.mount_gradio_app(
        FastAPI(),
        demo,
        path="/",
        allowed_paths=[str(Path(app.HISTORY_STORE.artifact_root).resolve())],
    )

    async def request_advisory_path():
        transport = ASGITransport(app=server)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/static//windows/win.ini")

    response = asyncio.run(request_advisory_path())

    assert response.status_code in {403, 404}
    assert "for 16-bit app support" not in response.text.lower()
