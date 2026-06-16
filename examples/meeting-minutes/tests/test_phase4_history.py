"""Phase 4 — Unit test MinutesHistoryService (lưu speaker + summary)."""

import json
import os

import pytest

from minutes_history_service import MinutesHistoryService


@pytest.fixture
def svc(tmp_path):
    return MinutesHistoryService(recordings_dir=str(tmp_path))


def _msg(speaker, text, is_final=True, ts="2026-06-16T10:00:00Z"):
    return {"speaker": speaker, "text": text, "timestamp": ts, "is_final": is_final}


def test_start_save_end_writes_json_with_speaker(svc, tmp_path):
    svc.start_session("minutes_vi_20260616_100000")
    svc.save_transcript(_msg("S1", "Chào mọi người"))
    svc.save_transcript(_msg("S2", "Bắt đầu họp nhé"))
    path = svc.end_session()

    assert path and os.path.exists(path)
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["language"] == "vi"
    assert data["summary"] is None
    assert len(data["transcripts"]) == 2
    assert data["transcripts"][0]["speaker"] == "S1"
    assert data["transcripts"][1]["text"] == "Bắt đầu họp nhé"


def test_vietnamese_unicode_preserved(svc):
    svc.start_session("minutes_vi_20260616_101000")
    svc.save_transcript(_msg("S1", "Tiếng Việt có dấu đầy đủ"))
    path = svc.end_session()
    raw = open(path, encoding="utf-8").read()
    # ensure_ascii=False -> ký tự tiếng Việt giữ nguyên, không bị \uXXXX
    assert "Tiếng Việt có dấu đầy đủ" in raw


def test_save_without_session_is_noop(svc):
    # Không có session -> không raise, không tạo file
    svc.save_transcript(_msg("S1", "lạc"))
    assert svc.end_session() is None


def test_load_transcripts_only_final(svc):
    svc.start_session("minutes_vi_20260616_102000")
    svc.save_transcript(_msg("S1", "final 1", is_final=True))
    svc.save_transcript(_msg("S1", "interim", is_final=False))
    svc.end_session()
    lines = svc.load_transcripts("minutes_vi_20260616_102000")
    assert len(lines) == 1
    assert lines[0]["text"] == "final 1"


def test_update_and_get_summary(svc):
    sid = "minutes_vi_20260616_103000"
    svc.start_session(sid)
    svc.save_transcript(_msg("S1", "nội dung"))
    svc.end_session()

    assert svc.get_summary(sid) is None
    ok = svc.update_summary(sid, "## Biên bản\n- Quyết định: xong")
    assert ok is True
    assert "Biên bản" in svc.get_summary(sid)


def test_update_summary_missing_session_returns_false(svc):
    assert svc.update_summary("minutes_vi_không_tồn_tại", "x") is False


def test_get_all_recordings_only_minutes_prefix(svc, tmp_path):
    # Tạo 1 file minutes_* và 1 file meeting_* (của màn hình cũ) -> chỉ list minutes_*
    open(os.path.join(tmp_path, "minutes_vi_20260616_104000.wav"), "wb").close()
    open(os.path.join(tmp_path, "meeting_ja_20251126_211110.wav"), "wb").close()
    recs = svc.get_all_recordings()
    ids = [r["id"] for r in recs]
    assert ids == ["minutes_vi_20260616_104000"]
    assert recs[0]["language"] == "vi"
    assert recs[0]["has_transcript"] is False


def test_get_recording_detail(svc):
    sid = "minutes_vi_20260616_105000"
    svc.start_session(sid)
    svc.save_transcript(_msg("S1", "câu một"))
    svc.end_session()
    svc.update_summary(sid, "tóm tắt")

    detail = svc.get_recording_detail(sid)
    assert detail["id"] == sid
    assert detail["summary"] == "tóm tắt"
    assert detail["transcripts"][0]["speaker"] == "S1"


def test_get_recording_detail_missing_returns_none(svc):
    assert svc.get_recording_detail("minutes_vi_99999999_999999") is None


def test_broadcaster_saves_into_history_singleton(monkeypatch, tmp_path):
    """Tích hợp: SpeakerTranscriptBroadcaster._save_to_history ghi vào singleton."""
    import minutes_history_service as mod
    from minutes_bot import SpeakerTranscriptBroadcaster

    # Thay singleton bằng instance dùng tmp dir
    test_svc = MinutesHistoryService(recordings_dir=str(tmp_path))
    monkeypatch.setattr(mod, "minutes_history_service", test_svc)

    test_svc.start_session("minutes_vi_20260616_110000")
    b = SpeakerTranscriptBroadcaster()
    b._save_to_history({"speaker": "S1", "text": "xin chào", "is_final": True})
    assert len(test_svc._session_data["transcripts"]) == 1
    assert test_svc._session_data["transcripts"][0]["speaker"] == "S1"
