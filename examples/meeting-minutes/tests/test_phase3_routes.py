"""Phase 3 — Unit test routes & websocket /minutes (FastAPI TestClient)."""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

import minutes_bot
import minutes_routes
from minutes_history_service import MinutesHistoryService


@pytest.fixture
def client(tmp_path, monkeypatch):
    # History service ghi vào thư mục tạm
    test_hist = MinutesHistoryService(recordings_dir=str(tmp_path))
    monkeypatch.setattr(minutes_routes, "minutes_history_service", test_hist)

    # Reset state global giữa các test
    minutes_bot.minutes_recording_state.update(
        {"is_recording": False, "current_filename": None, "session_id": None}
    )
    minutes_bot.minutes_transcript_broadcaster._websockets.clear()
    minutes_bot.minutes_audio_recorder_instance = None

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")

    @app.get("/test-login")
    async def _login(request: Request):
        request.session["authenticated"] = True
        return {"ok": True}

    minutes_routes.setup_minutes_routes(app, small_webrtc_handler=None)
    c = TestClient(app)
    c._hist = test_hist  # tiện truy cập trong test
    return c


def _login(client):
    assert client.get("/test-login").status_code == 200


# --------------------------- auth gating ---------------------------------
def test_status_requires_auth(client):
    assert client.get("/api/minutes/status").status_code == 401


def test_status_after_login(client):
    _login(client)
    r = client.get("/api/minutes/status")
    assert r.status_code == 200
    assert r.json() == {"is_recording": False, "language": "vi"}


# --------------------------- start/stop ----------------------------------
def test_start_recording_sets_state_and_session(client):
    _login(client)
    r = client.post("/api/minutes/start-recording")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "recording"
    assert body["filename"].startswith("minutes_vi_")
    assert body["filename"].endswith(".wav")
    assert minutes_bot.minutes_recording_state["is_recording"] is True
    # history session đã mở
    assert client._hist._current_session_id == body["session_id"]


def test_stop_recording_ends_session_and_triggers_summary(client, monkeypatch):
    _login(client)
    called = {}
    monkeypatch.setattr(
        minutes_routes,
        "generate_minutes_summary",
        lambda sid, **kw: called.setdefault("sid", sid) or "fake summary",
    )

    start = client.post("/api/minutes/start-recording").json()
    r = client.post("/api/minutes/stop-recording")
    assert r.status_code == 200
    assert r.json()["session_id"] == start["session_id"]
    assert minutes_bot.minutes_recording_state["is_recording"] is False
    # Background task đã chạy sinh biên bản đúng session
    assert called.get("sid") == start["session_id"]


def test_stop_recording_summary_error_does_not_break(client, monkeypatch):
    _login(client)

    def boom(sid, **kw):
        raise RuntimeError("LLM lỗi")

    monkeypatch.setattr(minutes_routes, "generate_minutes_summary", boom)
    client.post("/api/minutes/start-recording")
    # Vẫn trả 200 dù sinh biên bản lỗi (lỗi nuốt trong background)
    assert client.post("/api/minutes/stop-recording").status_code == 200


# --------------------------- summary / history ---------------------------
def test_get_summary_endpoint(client):
    _login(client)
    sid = "minutes_vi_20260616_130000"
    client._hist.start_session(sid)
    client._hist.save_transcript({"speaker": "S1", "text": "x", "is_final": True})
    client._hist.end_session()
    client._hist.update_summary(sid, "## Biên bản\nOK")

    r = client.get(f"/api/minutes/summary/{sid}")
    assert r.status_code == 200
    assert "Biên bản" in r.json()["summary"]


def test_history_list_and_detail(client, tmp_path):
    _login(client)
    sid = "minutes_vi_20260616_131000"
    # tạo file wav giả + session
    open(tmp_path / f"{sid}.wav", "wb").close()
    client._hist.start_session(sid)
    client._hist.save_transcript({"speaker": "S2", "text": "nội dung", "is_final": True})
    client._hist.end_session()

    lst = client.get("/api/minutes/history").json()
    assert any(item["id"] == sid for item in lst)

    detail = client.get(f"/api/minutes/history/{sid}")
    assert detail.status_code == 200
    assert detail.json()["transcripts"][0]["speaker"] == "S2"


def test_history_detail_missing_404(client):
    _login(client)
    assert client.get("/api/minutes/history/minutes_vi_0").status_code == 404


# --------------------------- websocket -----------------------------------
def test_websocket_registers_and_unregisters(client):
    b = minutes_bot.minutes_transcript_broadcaster
    assert b.websocket_count == 0
    with client.websocket_connect("/ws/minutes-transcripts"):
        # đã accept + đăng ký
        assert b.websocket_count == 1
    # sau khi đóng -> gỡ
    assert b.websocket_count == 0


# --------------------------- page & offer --------------------------------
def test_minutes_page_redirects_when_not_authed(client):
    r = client.get("/minutes", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/"


def test_offer_without_handler_returns_503(client):
    # handler=None -> 503 (không cần webrtc extra)
    r = client.post("/api/minutes/offer", json={"sdp": "x", "type": "offer"})
    assert r.status_code == 503
