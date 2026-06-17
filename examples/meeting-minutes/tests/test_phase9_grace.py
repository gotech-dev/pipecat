"""Phase 9 — Grace window: các câu final MUỘN của STT không bị rớt khi Stop.

Speechmatics (ENHANCED + diarization) trả final trễ vài giây. Trước đây Stop đóng
session ngay -> đuôi cuộc họp bị rớt khỏi transcript -> biên bản thiếu ý. Các test
này chứng minh finalize_and_summarize giữ session mở trong lúc chờ grace nên câu
final muộn vẫn được lưu, và không đè nhầm khi đã có phiên mới.
"""

import json
import os

import pytest

from minutes_history_service import MinutesHistoryService
import minutes_routes


@pytest.mark.asyncio
async def test_finalize_captures_late_final(monkeypatch, tmp_path):
    svc = MinutesHistoryService(recordings_dir=str(tmp_path))
    monkeypatch.setattr(minutes_routes, "minutes_history_service", svc)

    sid = "minutes_vi_20260616_200000"
    svc.start_session(sid)
    # Câu đã final TRƯỚC khi bấm Stop
    svc.save_transcript({"speaker": "S1", "text": "Câu đầu", "is_final": True})

    # Mô phỏng câu final MUỘN tới trong lúc chờ grace (session vẫn mở)
    async def fake_sleep(_secs):
        svc.save_transcript(
            {"speaker": "S1", "text": "Câu cuối tới muộn", "is_final": True}
        )

    monkeypatch.setattr(minutes_routes.asyncio, "sleep", fake_sleep)

    # Không gọi LLM thật trong test
    called = {}

    def fake_summary(session_id):
        called["sid"] = session_id

    monkeypatch.setattr(minutes_routes, "run_summary_for_session", fake_summary)

    await minutes_routes.finalize_and_summarize(sid)

    # Session đã đóng, JSON chứa CẢ câu đầu lẫn câu cuối tới muộn
    json_path = os.path.join(str(tmp_path), f"{sid}.json")
    assert os.path.exists(json_path)
    data = json.loads(open(json_path, encoding="utf-8").read())
    texts = [t["text"] for t in data["transcripts"]]
    assert texts == ["Câu đầu", "Câu cuối tới muộn"]
    assert called["sid"] == sid


@pytest.mark.asyncio
async def test_finalize_skips_when_session_replaced(monkeypatch, tmp_path):
    """Nếu phiên mới đã mở trong lúc chờ -> đóng-trễ phiên cũ không đè phiên mới."""
    svc = MinutesHistoryService(recordings_dir=str(tmp_path))
    monkeypatch.setattr(minutes_routes, "minutes_history_service", svc)

    old_sid = "minutes_vi_OLD"
    new_sid = "minutes_vi_NEW"

    async def fake_sleep(_secs):
        # Trong lúc chờ grace của phiên cũ, người dùng đã mở phiên mới
        svc.start_session(new_sid)
        svc.save_transcript({"speaker": "S1", "text": "phiên mới", "is_final": True})

    monkeypatch.setattr(minutes_routes.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(minutes_routes, "run_summary_for_session", lambda s: None)

    svc.start_session(old_sid)
    await minutes_routes.finalize_and_summarize(old_sid)

    # Phiên mới vẫn đang mở (không bị đóng nhầm)
    assert svc._current_session_id == new_sid


def test_stop_grace_secs_default_and_override(monkeypatch):
    monkeypatch.delenv("MINUTES_STOP_GRACE_SECS", raising=False)
    assert minutes_routes._stop_grace_secs() == minutes_routes.DEFAULT_STOP_GRACE_SECS

    monkeypatch.setenv("MINUTES_STOP_GRACE_SECS", "2.5")
    assert minutes_routes._stop_grace_secs() == 2.5

    # Giá trị rác/âm -> rơi về mặc định (an toàn)
    monkeypatch.setenv("MINUTES_STOP_GRACE_SECS", "abc")
    assert minutes_routes._stop_grace_secs() == minutes_routes.DEFAULT_STOP_GRACE_SECS
    monkeypatch.setenv("MINUTES_STOP_GRACE_SECS", "-3")
    assert minutes_routes._stop_grace_secs() == minutes_routes.DEFAULT_STOP_GRACE_SECS
