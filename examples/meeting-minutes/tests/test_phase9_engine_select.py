"""Phase 9 — Unit test chọn engine STT (premium vs internal) cho màn /minutes.

Không kết nối mạng: chỉ kiểm tra logic chọn engine và khởi tạo service nội bộ.
"""

import pytest

from pipecat.processors.frame_processor import FrameProcessor
from pipecat.transcriptions.language import Language

import minutes_bot
from gotech_asr_stt import GoTechASRSTTService
from minutes_bot import build_minutes_pipeline, build_minutes_stt
from streaming_recorder import StreamingAudioRecorder


class _FakeTransport:
    def input(self):
        class _Identity(FrameProcessor):
            async def process_frame(self, frame, direction):
                await super().process_frame(frame, direction)
                await self.push_frame(frame, direction)

        return _Identity()


# --------------------------- engine selection ---------------------------
@pytest.fixture
def _spy(monkeypatch):
    calls = []
    monkeypatch.setattr(
        minutes_bot, "build_speechmatics_stt", lambda *a, **k: calls.append("premium") or "SPEECHMATICS"
    )
    monkeypatch.setattr(
        minutes_bot, "build_internal_asr_stt", lambda *a, **k: calls.append("internal") or "INTERNAL"
    )
    return calls


def test_engine_premium_uses_speechmatics(_spy):
    assert build_minutes_stt("premium") == "SPEECHMATICS"
    assert _spy == ["premium"]


def test_engine_internal_uses_gotech(_spy):
    assert build_minutes_stt("internal") == "INTERNAL"
    assert _spy == ["internal"]


@pytest.mark.parametrize("bad", ["xyz", "", None, "Premium", "INTERNAL"])
def test_engine_invalid_falls_back_to_premium(_spy, bad):
    # Chỉ đúng chuỗi "internal" mới dùng nội bộ; còn lại -> premium (an toàn).
    assert build_minutes_stt(bad) == "SPEECHMATICS"
    assert _spy == ["premium"]


def test_default_engine_state_is_premium():
    assert minutes_bot.minutes_recording_state.get("engine") == "premium"


# --------------------------- internal STT service -----------------------
def test_build_internal_asr_stt_returns_service():
    svc = minutes_bot.build_internal_asr_stt()
    assert isinstance(svc, GoTechASRSTTService)
    assert svc._language == Language.VI


def test_internal_stt_defaults(monkeypatch):
    for k in ("GOTECH_ASR_BASE_URL", "GOTECH_ASR_MODEL", "GOTECH_ASR_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    svc = GoTechASRSTTService()
    assert svc._endpoint == "https://gateway.gotechjsc.com/v1/audio/transcriptions"
    assert svc._model == "nemotron-asr"


def test_internal_stt_env_override(monkeypatch):
    monkeypatch.setenv("GOTECH_ASR_BASE_URL", "https://example.com/v1/")
    monkeypatch.setenv("GOTECH_ASR_MODEL", "custom-asr")
    svc = GoTechASRSTTService()
    # base_url được rstrip("/") trước khi nối path
    assert svc._endpoint == "https://example.com/v1/audio/transcriptions"
    assert svc._model == "custom-asr"


def test_internal_stt_fits_pipeline(tmp_path):
    """Service nội bộ là FrameProcessor hợp lệ -> lắp được vào pipeline /minutes."""
    stt = GoTechASRSTTService()
    recorder = StreamingAudioRecorder(output_dir=str(tmp_path))
    pipeline = build_minutes_pipeline(_FakeTransport(), stt, recorder)
    assert stt in list(pipeline.processors)
