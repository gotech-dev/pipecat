"""Phase 5 — Unit test sinh biên bản AI (mock LLM, không gọi mạng)."""

from types import SimpleNamespace

import pytest

from minutes_history_service import MinutesHistoryService
import minutes_summary
from minutes_summary import (
    build_generation_config,
    build_summary_prompt,
    format_transcript_for_prompt,
    generate_minutes_summary,
    generate_summary_text,
    speaker_display,
    _extract_text,
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
    def __init__(self, text="## Tóm tắt\nOK", record=None):
        self.record = record if record is not None else {}
        self.models = FakeModels(text, self.record)


class BoomClient:
    class models:
        @staticmethod
        def generate_content(**kwargs):
            raise RuntimeError("API down")


# --------------------------- speaker_display -----------------------------
def test_speaker_display_default_mapping():
    assert speaker_display("S1") == "Người 1"
    assert speaker_display("S12") == "Người 12"


def test_speaker_display_name_map_override():
    assert speaker_display("S1", {"S1": "Anh Nam"}) == "Anh Nam"


def test_speaker_display_unknown_and_empty():
    assert speaker_display("") == "Không rõ"
    assert speaker_display("X9") == "X9"


# --------------------------- format / prompt -----------------------------
def test_format_transcript_skips_empty():
    transcripts = [
        {"speaker": "S1", "text": "Chào"},
        {"speaker": "S2", "text": "  "},
        {"speaker": "S2", "text": "Họp thôi"},
    ]
    out = format_transcript_for_prompt(transcripts)
    assert out == "Người 1: Chào\nNgười 2: Họp thôi"


def test_build_summary_prompt_has_sections_and_text():
    prompt = build_summary_prompt("Người 1: nội dung abc")
    assert "Quyết định đã thống nhất" in prompt
    assert "Việc cần làm" in prompt
    assert "nội dung abc" in prompt


def test_build_summary_prompt_emphasizes_completeness():
    """Prompt phải ép ĐẦY ĐỦ (không bỏ sót ý) -> chống biên bản thiếu ý."""
    prompt = build_summary_prompt("Người 1: abc")
    assert "ĐẦY ĐỦ" in prompt
    assert "KHÔNG bỏ sót" in prompt
    # Phải nhắc model đọc cả đoạn cuối (nơi hay bị mất do STT trả final trễ)
    assert "ĐOẠN CUỐI" in prompt


# --------------------------- generation config ---------------------------
def test_build_generation_config_defaults(monkeypatch):
    monkeypatch.delenv("MINUTES_SUMMARY_TEMPERATURE", raising=False)
    monkeypatch.delenv("MINUTES_SUMMARY_MAX_TOKENS", raising=False)
    cfg = build_generation_config()
    assert cfg["temperature"] == minutes_summary.DEFAULT_TEMPERATURE
    assert cfg["max_output_tokens"] == minutes_summary.DEFAULT_MAX_OUTPUT_TOKENS


def test_build_generation_config_env_override(monkeypatch):
    monkeypatch.setenv("MINUTES_SUMMARY_TEMPERATURE", "0.5")
    monkeypatch.setenv("MINUTES_SUMMARY_MAX_TOKENS", "1234")
    cfg = build_generation_config()
    assert cfg["temperature"] == 0.5
    assert cfg["max_output_tokens"] == 1234


def test_build_generation_config_bad_env_falls_back(monkeypatch):
    monkeypatch.setenv("MINUTES_SUMMARY_TEMPERATURE", "xx")
    monkeypatch.setenv("MINUTES_SUMMARY_MAX_TOKENS", "yy")
    cfg = build_generation_config()
    assert cfg["temperature"] == minutes_summary.DEFAULT_TEMPERATURE
    assert cfg["max_output_tokens"] == minutes_summary.DEFAULT_MAX_OUTPUT_TOKENS


def test_generate_summary_text_passes_config(monkeypatch):
    """generate_summary_text phải truyền config (temperature/max_tokens) vào API."""
    monkeypatch.delenv("MINUTES_SUMMARY_TEMPERATURE", raising=False)
    monkeypatch.delenv("MINUTES_SUMMARY_MAX_TOKENS", raising=False)
    transcripts = [{"speaker": "S1", "text": "Chào"}]
    client = FakeClient(text="ok")
    generate_summary_text(transcripts, client=client, model="m")
    assert "config" in client.record
    assert client.record["config"]["temperature"] == minutes_summary.DEFAULT_TEMPERATURE


def test_extract_text_uses_text_attr():
    obj = SimpleNamespace(text="A")
    assert _extract_text(obj) == "A"


def test_extract_text_candidates_fallback():
    part = SimpleNamespace(text="B")
    cand = SimpleNamespace(content=SimpleNamespace(parts=[part]))
    obj = SimpleNamespace(text=None, candidates=[cand])
    assert _extract_text(obj) == "B"


# --------------------------- generate_summary_text -----------------------
def test_generate_summary_text_with_mock_client():
    transcripts = [{"speaker": "S1", "text": "Chào cả nhà"}]
    client = FakeClient(text="## Tóm tắt nội dung\nĐã chào hỏi")
    out = generate_summary_text(transcripts, client=client, model="test-model")
    assert "Tóm tắt nội dung" in out
    # Model được truyền đúng vào API
    assert client.record["model"] == "test-model"


def test_generate_summary_text_empty_transcript_returns_none():
    assert generate_summary_text([], client=FakeClient()) is None


def test_generate_summary_text_no_key_no_client(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    transcripts = [{"speaker": "S1", "text": "abc"}]
    assert generate_summary_text(transcripts, client=None) is None


def test_generate_summary_text_llm_error_returns_none():
    transcripts = [{"speaker": "S1", "text": "abc"}]
    assert generate_summary_text(transcripts, client=BoomClient()) is None


# --------------------------- orchestrator --------------------------------
def test_generate_minutes_summary_writes_to_history(tmp_path):
    svc = MinutesHistoryService(recordings_dir=str(tmp_path))
    sid = "minutes_vi_20260616_120000"
    svc.start_session(sid)
    svc.save_transcript({"speaker": "S1", "text": "Quyết định triển khai", "is_final": True})
    svc.end_session()

    client = FakeClient(text="## Quyết định đã thống nhất\nTriển khai")
    out = generate_minutes_summary(sid, history=svc, client=client)
    assert "Triển khai" in out
    # Đã ghi vào file JSON
    assert "Triển khai" in svc.get_summary(sid)


def test_generate_minutes_summary_no_transcript_returns_none(tmp_path):
    svc = MinutesHistoryService(recordings_dir=str(tmp_path))
    out = generate_minutes_summary("minutes_vi_khong_co", history=svc, client=FakeClient())
    assert out is None


def test_generate_minutes_summary_uses_default_history(monkeypatch, tmp_path):
    """Khi không truyền history -> dùng singleton minutes_history_service."""
    svc = MinutesHistoryService(recordings_dir=str(tmp_path))
    sid = "minutes_vi_20260616_121000"
    svc.start_session(sid)
    svc.save_transcript({"speaker": "S1", "text": "abc", "is_final": True})
    svc.end_session()

    import minutes_history_service as mod
    monkeypatch.setattr(mod, "minutes_history_service", svc)

    out = generate_minutes_summary(sid, client=FakeClient(text="ok"))
    assert out == "ok"
