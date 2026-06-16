"""Phase 7 — Tích hợp: không trùng route với /meeting + bot.py hợp lệ & đã wire."""

import os
import py_compile

import pytest
from fastapi import FastAPI

import minutes_routes

EXAMPLE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Các path của màn hình /meeting CŨ (không được trùng)
OLD_PATHS = {
    "/",
    "/meeting",
    "/api/offer",
    "/api/start-recording",
    "/api/stop-recording",
    "/api/status",
    "/api/login",
    "/api/logout",
    "/ws/transcripts",
    "/history",
    "/api/history",
}


def _minutes_paths():
    app = FastAPI()
    minutes_routes.setup_minutes_routes(app, small_webrtc_handler=None)
    return {r.path for r in app.routes if hasattr(r, "path")}


def test_no_route_collision_with_old_screen():
    paths = _minutes_paths()
    collisions = paths & OLD_PATHS
    assert not collisions, f"Route bị trùng với màn hình cũ: {collisions}"


def test_minutes_routes_all_namespaced():
    # Mọi route mới phải nằm trong namespace /minutes hoặc /api/minutes hoặc ws minutes
    paths = _minutes_paths()
    new_paths = paths - {
        # FastAPI tự thêm vài route mặc định (docs...) -> bỏ qua
        "/openapi.json",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
    }
    for p in new_paths:
        assert (
            p.startswith("/api/minutes")
            or p.startswith("/minutes")
            or p == "/ws/minutes-transcripts"
        ), f"Route không thuộc namespace /minutes: {p}"


def test_expected_minutes_endpoints_present():
    paths = _minutes_paths()
    for expected in [
        "/api/minutes/offer",
        "/api/minutes/start-recording",
        "/api/minutes/stop-recording",
        "/api/minutes/status",
        "/ws/minutes-transcripts",
        "/minutes",
    ]:
        assert expected in paths, f"Thiếu route {expected}"


def test_bot_py_wires_minutes_routes():
    src = open(os.path.join(EXAMPLE_DIR, "bot.py"), encoding="utf-8").read()
    assert "from minutes_routes import setup_minutes_routes" in src
    assert "setup_minutes_routes(app, small_webrtc_handler)" in src


@pytest.mark.parametrize(
    "module",
    [
        "bot.py",
        "minutes_bot.py",
        "minutes_routes.py",
        "minutes_history_service.py",
        "minutes_summary.py",
        "minutes_transport.py",
    ],
)
def test_modules_compile(module):
    """py_compile xác nhận không có lỗi cú pháp (không cần import heavy deps)."""
    py_compile.compile(os.path.join(EXAMPLE_DIR, module), doraise=True)
