"""Phase 8 — Trang lịch sử /minutes/history + link điều hướng."""

import os

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

import minutes_routes

EXAMPLE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(EXAMPLE_DIR, "static")


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


# --------------------------- route /minutes/history ----------------------
def test_history_page_file_exists():
    assert os.path.exists(os.path.join(STATIC, "minutes_history.html"))


def test_history_page_requires_auth_redirect(client):
    r = client.get("/minutes/history", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/"


def test_history_page_served_when_authed(client):
    client.get("/test-login")
    r = client.get("/minutes/history")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_history_page_uses_minutes_api(client):
    client.get("/test-login")
    body = client.get("/minutes/history").text
    assert "/api/minutes/history" in body
    assert "speakerDisplay" in body  # render theo người nói
    assert "Biên bản AI" in body
    # Dùng mảng trực tiếp (không phải data.recordings của màn cũ)
    assert "Array.isArray(recordings)" in body


# --------------------------- link điều hướng -----------------------------
def test_index_links_to_minutes():
    body = open(os.path.join(STATIC, "index.html"), encoding="utf-8").read()
    assert 'href="/minutes"' in body


def test_minutes_links_to_history_and_meeting():
    body = open(os.path.join(STATIC, "minutes.html"), encoding="utf-8").read()
    assert 'href="/minutes/history"' in body
    assert 'href="/meeting"' in body


def test_history_page_back_link_to_minutes():
    body = open(os.path.join(STATIC, "minutes_history.html"), encoding="utf-8").read()
    assert 'href="/minutes"' in body
