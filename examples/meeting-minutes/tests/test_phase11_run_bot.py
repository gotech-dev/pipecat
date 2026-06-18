"""Phase 11 — Test end-to-end run_minutes_bot: chọn engine, đăng ký handler, chạy.

Mock PipelineRunner + recorder + transport để KHÔNG chạy pipeline/microphone thật,
nhưng vẫn đi qua toàn bộ logic dựng bot (engine selection, event handlers).
"""

import sys
import types

import pytest

from pipecat.processors.frame_processor import FrameProcessor

import minutes_bot as mb


class _Identity(FrameProcessor):
    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


class _FakeTransport:
    def __init__(self):
        self.handlers = {}

    def event_handler(self, name):
        def deco(fn):
            self.handlers[name] = fn
            return fn

        return deco

    def input(self):
        return _Identity()


class _FakeRecorder(FrameProcessor):
    def __init__(self, *a, **k):
        super().__init__()
        self.started = None
        self.stopped = False

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)

    async def start_recording(self, filename):
        self.started = filename

    async def stop_recording(self):
        self.stopped = True


class _FakeRunner:
    last = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _FakeRunner.last = self

    async def run(self, task):
        self.ran_task = task


@pytest.fixture
def _patched(monkeypatch):
    """Mock các thành phần chạy thật để cô lập logic dựng bot."""
    captured = {}

    async def fake_build(engine):
        captured["engine"] = engine
        return _Identity()

    monkeypatch.setattr(mb, "build_minutes_stt", fake_build)
    monkeypatch.setattr(mb, "StreamingAudioRecorder", _FakeRecorder)
    monkeypatch.setattr(mb, "PipelineRunner", _FakeRunner)
    # reset state
    mb.minutes_audio_recorder_instance = None
    mb.minutes_recording_state.update(
        {"is_recording": False, "current_filename": None, "session_id": None, "engine": "premium"}
    )
    return captured


def _args():
    return types.SimpleNamespace(handle_sigint=False, pipeline_idle_timeout_secs=0)


@pytest.mark.asyncio
async def test_run_bot_uses_internal_engine(_patched):
    mb.minutes_recording_state["engine"] = "internal"
    transport = _FakeTransport()
    await mb.run_minutes_bot(transport, _args())

    assert _patched["engine"] == "internal"  # đúng engine đã chọn
    assert _FakeRunner.last.ran_task is not None  # runner đã chạy task
    # 2 event handler được đăng ký
    assert "on_client_connected" in transport.handlers
    assert "on_client_disconnected" in transport.handlers


@pytest.mark.asyncio
async def test_run_bot_uses_premium_engine_by_default(_patched):
    transport = _FakeTransport()
    await mb.run_minutes_bot(transport, _args())
    assert _patched["engine"] == "premium"


@pytest.mark.asyncio
async def test_on_client_connected_starts_recording_when_recording(_patched):
    mb.minutes_recording_state["is_recording"] = True
    mb.minutes_recording_state["current_filename"] = "rec.wav"
    transport = _FakeTransport()
    await mb.run_minutes_bot(transport, _args())

    await transport.handlers["on_client_connected"](transport, object())
    assert mb.minutes_audio_recorder_instance.started == "rec.wav"


@pytest.mark.asyncio
async def test_on_client_connected_generates_filename_if_missing(_patched):
    mb.minutes_recording_state["is_recording"] = True
    mb.minutes_recording_state["current_filename"] = None
    transport = _FakeTransport()
    await mb.run_minutes_bot(transport, _args())

    await transport.handlers["on_client_connected"](transport, object())
    started = mb.minutes_audio_recorder_instance.started
    assert started and started.startswith("minutes_vi_") and started.endswith(".wav")


@pytest.mark.asyncio
async def test_on_client_connected_noop_when_not_recording(_patched):
    mb.minutes_recording_state["is_recording"] = False
    transport = _FakeTransport()
    await mb.run_minutes_bot(transport, _args())

    await transport.handlers["on_client_connected"](transport, object())
    assert mb.minutes_audio_recorder_instance.started is None


@pytest.mark.asyncio
async def test_on_client_disconnected_stops_recording(_patched):
    transport = _FakeTransport()
    await mb.run_minutes_bot(transport, _args())

    await transport.handlers["on_client_disconnected"](transport, object())
    assert mb.minutes_audio_recorder_instance.stopped is True


@pytest.mark.asyncio
async def test_minutes_bot_entry_creates_transport_and_runs(monkeypatch, _patched):
    created = {}

    async def fake_create_transport(runner_args, params):
        created["called"] = True
        return _FakeTransport()

    # create_transport được import lazy bên trong hàm -> patch tại nguồn
    import pipecat.runner.utils as runner_utils

    monkeypatch.setattr(runner_utils, "create_transport", fake_create_transport)

    # minutes_transport import SileroVADAnalyzer (cần onnxruntime - không có trong
    # venv test) -> tiêm module giả để cô lập, không phụ thuộc VAD thật.
    fake_transport_mod = types.ModuleType("minutes_transport")
    fake_transport_mod.minutes_transport_params = {}
    monkeypatch.setitem(sys.modules, "minutes_transport", fake_transport_mod)

    await mb.minutes_bot_entry(_args())
    assert created["called"] is True
    assert _FakeRunner.last.ran_task is not None
