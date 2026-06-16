"""Phase 1 — Unit test SpeakerTranscriptBroadcaster + frame_to_message."""

import pytest

from pipecat.frames.frames import InterimTranscriptionFrame, TranscriptionFrame
from pipecat.transcriptions.language import Language

import minutes_bot
from minutes_bot import SpeakerTranscriptBroadcaster, frame_to_message


# --------------------------- Fakes ---------------------------------------
class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, message):
        self.sent.append(message)


class BrokenWebSocket:
    async def send_json(self, message):
        raise RuntimeError("connection closed")


def make_final(text="xin chào", speaker="S1", ts="2026-06-16T10:00:00Z"):
    return TranscriptionFrame(text, speaker, ts, Language.VI)


def make_interim(text="xin", speaker="S2", ts="2026-06-16T10:00:01Z"):
    return InterimTranscriptionFrame(text, speaker, ts)


# --------------------------- frame_to_message ----------------------------
def test_frame_to_message_final():
    msg = frame_to_message(make_final(text="họp nào", speaker="S1"))
    assert msg["type"] == "transcription"
    assert msg["speaker"] == "S1"
    assert msg["text"] == "họp nào"
    assert msg["is_final"] is True
    assert msg["language"] == "vi"


def test_frame_to_message_interim():
    msg = frame_to_message(make_interim(text="xin ch", speaker="S2"))
    assert msg["speaker"] == "S2"
    assert msg["is_final"] is False


def test_frame_to_message_empty_speaker_defaults_to_blank():
    # user_id rỗng -> speaker = "" (không vỡ)
    frame = TranscriptionFrame("text", "", "ts", Language.VI)
    assert frame_to_message(frame)["speaker"] == ""


# --------------------------- websocket mgmt ------------------------------
def test_add_remove_websocket():
    b = SpeakerTranscriptBroadcaster()
    ws = FakeWebSocket()
    b.add_websocket(ws)
    assert b.websocket_count == 1
    b.remove_websocket(ws)
    assert b.websocket_count == 0
    # remove lần nữa không lỗi
    b.remove_websocket(ws)
    assert b.websocket_count == 0


# --------------------------- _broadcast ----------------------------------
@pytest.mark.asyncio
async def test_broadcast_sends_to_websocket():
    b = SpeakerTranscriptBroadcaster()
    ws = FakeWebSocket()
    b.add_websocket(ws)
    await b._broadcast({"type": "transcription", "is_final": False, "text": "hi"})
    assert len(ws.sent) == 1
    assert ws.sent[0]["text"] == "hi"


@pytest.mark.asyncio
async def test_broadcast_no_websockets_no_crash():
    b = SpeakerTranscriptBroadcaster()
    # Không có ws nào -> không raise
    await b._broadcast({"type": "transcription", "is_final": False})


@pytest.mark.asyncio
async def test_broadcast_removes_failing_websocket():
    b = SpeakerTranscriptBroadcaster()
    good, bad = FakeWebSocket(), BrokenWebSocket()
    b.add_websocket(good)
    b.add_websocket(bad)
    await b._broadcast({"type": "transcription", "is_final": False, "text": "x"})
    # ws lỗi bị gỡ, ws tốt vẫn còn
    assert b.websocket_count == 1
    assert len(good.sent) == 1


@pytest.mark.asyncio
async def test_only_final_saved_to_history(monkeypatch):
    b = SpeakerTranscriptBroadcaster()
    saved = []
    monkeypatch.setattr(b, "_save_to_history", lambda m: saved.append(m))
    await b._broadcast({"is_final": False, "text": "interim"})
    await b._broadcast({"is_final": True, "text": "final"})
    assert len(saved) == 1
    assert saved[0]["text"] == "final"


def test_save_to_history_no_module_no_crash():
    # Phase 4 chưa có -> import lỗi được nuốt, không raise
    b = SpeakerTranscriptBroadcaster()
    b._save_to_history({"is_final": True, "text": "x"})


# --------------------------- process_frame (pipeline) --------------------
@pytest.mark.asyncio
async def test_process_frame_passthrough_and_broadcast():
    from pipecat.tests.utils import run_test

    b = SpeakerTranscriptBroadcaster()
    ws = FakeWebSocket()
    b.add_websocket(ws)

    frames_to_send = [make_final(text="kiểm thử", speaker="S1")]
    expected_down = [TranscriptionFrame]

    await run_test(
        b,
        frames_to_send=frames_to_send,
        expected_down_frames=expected_down,
    )

    # Frame được đẩy xuôi (sink nhận) VÀ được broadcast tới ws
    assert any(m.get("text") == "kiểm thử" and m.get("speaker") == "S1" for m in ws.sent)


def test_globals_isolated_from_old_screen():
    # State của /minutes phải là object riêng, không phải recording_state cũ
    assert "is_recording" in minutes_bot.minutes_recording_state
    assert minutes_bot.minutes_transcript_broadcaster is not None
