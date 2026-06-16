"""Phase 2 — Unit test cấu hình Speechmatics + dựng pipeline /minutes."""

import pytest

from pipecat.processors.frame_processor import FrameProcessor
from pipecat.services.speechmatics.stt import OperatingPoint
from pipecat.transcriptions.language import Language

import minutes_bot
from minutes_bot import (
    SpeakerTranscriptBroadcaster,
    build_minutes_pipeline,
    build_minutes_stt_params,
    build_speechmatics_stt,
)
from streaming_recorder import StreamingAudioRecorder


# --------------------------- helpers ------------------------------------
class _Identity(FrameProcessor):
    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class _FakeTransport:
    def input(self):
        return _Identity()


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for k in [
        "MINUTES_OPERATING_POINT",
        "MINUTES_SPEAKER_SENSITIVITY",
        "MINUTES_MAX_SPEAKERS",
    ]:
        monkeypatch.delenv(k, raising=False)


# --------------------------- STT params ---------------------------------
def test_stt_params_defaults_vietnamese_diarization():
    p = build_minutes_stt_params()
    assert p.language == Language.VI
    assert p.enable_diarization is True
    assert p.operating_point == OperatingPoint.ENHANCED
    assert p.speaker_sensitivity == 0.5
    assert p.max_speakers is None
    # Text giữ nguyên, speaker nằm ở user_id (không nhúng nhãn vào text)
    assert p.speaker_active_format == "{text}"
    assert p.speaker_passive_format == "{text}"


def test_stt_params_operating_point_standard(monkeypatch):
    monkeypatch.setenv("MINUTES_OPERATING_POINT", "standard")
    assert build_minutes_stt_params().operating_point == OperatingPoint.STANDARD


def test_stt_params_operating_point_unknown_falls_back_enhanced(monkeypatch):
    monkeypatch.setenv("MINUTES_OPERATING_POINT", "xyz")
    assert build_minutes_stt_params().operating_point == OperatingPoint.ENHANCED


def test_stt_params_max_speakers_and_sensitivity_from_env(monkeypatch):
    monkeypatch.setenv("MINUTES_MAX_SPEAKERS", "4")
    monkeypatch.setenv("MINUTES_SPEAKER_SENSITIVITY", "0.7")
    p = build_minutes_stt_params()
    assert p.max_speakers == 4
    assert p.speaker_sensitivity == 0.7


def test_env_int_invalid_returns_none(monkeypatch):
    monkeypatch.setenv("MINUTES_MAX_SPEAKERS", "abc")
    assert build_minutes_stt_params().max_speakers is None


def test_env_float_invalid_uses_default(monkeypatch):
    monkeypatch.setenv("MINUTES_SPEAKER_SENSITIVITY", "notnum")
    assert build_minutes_stt_params().speaker_sensitivity == 0.5


def test_build_speechmatics_stt_uses_api_key_arg():
    stt = build_speechmatics_stt(api_key="dummy-key")
    # Khởi tạo được, không kết nối mạng
    assert stt is not None


# --------------------------- pipeline -----------------------------------
def test_pipeline_order_and_membership(tmp_path):
    broadcaster = SpeakerTranscriptBroadcaster()
    stt = build_speechmatics_stt(api_key="dummy-key")
    recorder = StreamingAudioRecorder(output_dir=str(tmp_path))
    transport = _FakeTransport()

    pipeline = build_minutes_pipeline(transport, stt, recorder, broadcaster)
    procs = list(pipeline.processors)

    # 3 thành phần chính phải nằm trong pipeline
    assert stt in procs
    assert broadcaster in procs
    assert recorder in procs

    # Đúng thứ tự: STT -> broadcaster -> recorder
    assert procs.index(stt) < procs.index(broadcaster) < procs.index(recorder)


def test_pipeline_defaults_to_global_broadcaster(tmp_path):
    stt = build_speechmatics_stt(api_key="dummy-key")
    recorder = StreamingAudioRecorder(output_dir=str(tmp_path))
    pipeline = build_minutes_pipeline(_FakeTransport(), stt, recorder)
    assert minutes_bot.minutes_transcript_broadcaster in list(pipeline.processors)


def test_new_minutes_filename_format():
    name = minutes_bot._new_minutes_filename()
    assert name.startswith("minutes_vi_")
    assert name.endswith(".wav")
