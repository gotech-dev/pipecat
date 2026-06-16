"""Phase 6 — Kiểm tra trang /minutes serve đúng & tham chiếu đúng endpoint mới."""

import os

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

import minutes_routes

STATIC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static"
)


@pytest.fixture
def client():
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")

    @app.get("/test-login")
    async def _login(request: Request):
        request.session["authenticated"] = True
        return {"ok": True}

    minutes_routes.setup_minutes_routes(app, small_webrtc_handler=None)
    return TestClient(app)


def test_minutes_html_file_exists():
    assert os.path.exists(os.path.join(STATIC, "minutes.html"))


def test_minutes_page_served_when_authed(client):
    client.get("/test-login")
    r = client.get("/minutes")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_page_references_new_minutes_endpoints(client):
    client.get("/test-login")
    body = client.get("/minutes").text
    assert "/api/minutes/offer" in body
    assert "/api/minutes/start-recording" in body
    assert "/api/minutes/stop-recording" in body
    assert "/ws/minutes-transcripts" in body
    assert "/api/minutes/summary/" in body


def test_page_does_not_use_old_endpoints(client):
    client.get("/test-login")
    body = client.get("/minutes").text
    # Không được dùng endpoint của màn hình /meeting cũ
    assert "/api/start-recording" not in body
    assert "/api/stop-recording" not in body
    assert "/ws/transcripts" not in body  # /ws/minutes-transcripts là OK, không chứa chuỗi này


def test_page_has_speaker_rendering_and_summary_ui(client):
    client.get("/test-login")
    body = client.get("/minutes").text
    assert "speakerDisplay" in body  # gom nhóm theo người nói
    assert "Biên bản AI" in body
    assert body.strip().endswith("</html>")
