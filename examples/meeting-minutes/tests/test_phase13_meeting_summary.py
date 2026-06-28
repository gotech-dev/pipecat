"""Phase 13 — Biên bản AI SONG NGỮ cho màn /meeting (mock LLM, không gọi mạng).

Bao gồm:
- meeting_summary: helper thuần + sinh biên bản song ngữ (mock Gemini).
- history_service: load_transcripts / update_summary / get_summary.
- index.html (/meeting): có panel dịch riêng + khu tóm tắt + tham chiếu endpoint mới.
"""

import json
import os
from types import SimpleNamespace

import pytest

import meeting_summary
from meeting_summary import (
    build_bilingual_summary_prompt,
    format_meeting_transcript,
    generate_bilingual_summary_text,
    generate_meeting_summary,
    source_language_from_session,
)
from history_service import HistoryService

STATIC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static"
)


# --------------------------- Fake Gemini client --------------------------
class FakeModels:
    def __init__(self, text, record):
        self._text = text
        self._record = record

    def generate_content(self, **kwargs):
        self._record.update(kwargs)
        return SimpleNamespace(text=self._text)


class FakeClient:
    def __init__(self, text="# Biên bản (tiếng Nhật)\n...\n# Biên bản (Tiếng Việt)\n...", record=None):
        self.record = record if record is not None else {}
        self.models = FakeModels(text, self.record)


# ------------------------------- helpers ---------------------------------
def test_source_language_from_session():
    assert source_language_from_session("meeting_ja_20251208_065124") == "ja"
    assert source_language_from_session("meeting_en_20260101_000000") == "en"
    # fallback khi không parse được
    assert source_language_from_session("weird") == "ja"


def test_format_meeting_transcript_bo_qua_rong():
    txt = format_meeting_transcript(
        [{"text": "ご視聴"}, {"text": "  "}, {"text": "ありがとう"}, {"text": None}]
    )
    assert txt == "ご視聴\nありがとう"


def test_prompt_song_ngu_chua_du_2_phan():
    p = build_bilingual_summary_prompt("xin chào", "ja")
    assert "tiếng Nhật" in p  # tên ngôn ngữ gốc
    assert "# Biên bản (tiếng Nhật)" in p
    assert "# Biên bản (Tiếng Việt)" in p
    assert "xin chào" in p  # transcript được nhúng


def test_prompt_lang_anh():
    p = build_bilingual_summary_prompt("hello", "en")
    assert "tiếng Anh" in p


# ----------------------- generate (mock LLM) -----------------------------
def test_generate_bilingual_summary_text_ok():
    rec = {}
    client = FakeClient(text="# Biên bản (tiếng Nhật)\nA\n# Biên bản (Tiếng Việt)\nB", record=rec)
    out = generate_bilingual_summary_text([{"text": "hi"}], "ja", client=client)
    assert "Tiếng Việt" in out
    # prompt thực sự được gửi tới model
    assert "hi" in rec["contents"]


def test_generate_bilingual_summary_text_transcript_rong():
    assert generate_bilingual_summary_text([{"text": "   "}], "ja", client=FakeClient()) is None


def test_generate_meeting_summary_orchestrator(tmp_path):
    sid = "meeting_ja_20260101_000000"
    data = {
        "session_id": sid,
        "transcripts": [
            {"text": "おはよう", "is_final": True},
            {"text": "interim bỏ qua", "is_final": False},
            {"text": "ありがとう", "is_final": True},
        ],
        "translations": [],
        "summary": None,
    }
    (tmp_path / f"{sid}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    hs = HistoryService(recordings_dir=str(tmp_path))
    # chỉ lấy câu final
    assert len(hs.load_transcripts(sid)) == 2

    summary = generate_meeting_summary(sid, history=hs, client=FakeClient(text="OK-SUMMARY"))
    assert summary == "OK-SUMMARY"
    # đã ghi vào JSON
    saved = json.loads((tmp_path / f"{sid}.json").read_text(encoding="utf-8"))
    assert saved["summary"] == "OK-SUMMARY"
    assert hs.get_summary(sid) == "OK-SUMMARY"


def test_generate_meeting_summary_khong_co_transcript(tmp_path):
    hs = HistoryService(recordings_dir=str(tmp_path))
    assert generate_meeting_summary("meeting_ja_x", history=hs, client=FakeClient()) is None


# ----------------------- history_service.summary -------------------------
def test_history_service_summary_field_va_methods(tmp_path):
    hs = HistoryService(recordings_dir=str(tmp_path))
    hs.start_session("meeting_en_20260101_000000")
    assert hs._session_data["summary"] is None
    hs.save_transcript({"text": "hello", "is_final": True, "type": "transcription", "language": "en"})
    hs.end_session()
    # cập nhật summary sau khi đóng session
    assert hs.update_summary("meeting_en_20260101_000000", "BB") is True
    assert hs.get_summary("meeting_en_20260101_000000") == "BB"
    # session không tồn tại
    assert hs.update_summary("không_có", "x") is False
    assert hs.get_summary("không_có") is None


# ------------------------------- frontend --------------------------------
def _index_html() -> str:
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
        return f.read()


def test_index_co_panel_dich_rieng_va_khu_tom_tat():
    body = _index_html()
    assert 'id="translations"' in body  # panel dịch riêng
    assert 'id="summary"' in body  # khu tóm tắt
    assert 'id="summaryBtn"' in body  # nút tải .md


def test_index_tham_chieu_endpoint_summary():
    body = _index_html()
    assert "/api/summary/" in body
    assert "pollSummary" in body
    assert "downloadSummary" in body
