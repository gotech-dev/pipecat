"""Meeting Minutes (màn hình /minutes) — Biên bản cuộc họp tiếng Việt + tách người nói.

Màn hình này HOÀN TOÀN tách biệt với màn hình /meeting cũ (Gladia):
- State global riêng (minutes_*) để không tranh chấp với recording_state cũ.
- STT dùng Speechmatics với diarization -> mỗi câu kèm nhãn người nói (S1, S2...).
- Lưu lịch sử có speaker + sinh biên bản AI tóm tắt cuối cuộc họp.

File được xây dựng theo từng phase:
- Phase 1: state riêng + SpeakerTranscriptBroadcaster (file này).
- Phase 2: run_minutes_bot() (pipeline Speechmatics).
- Phase 3: setup_minutes_routes() (routes + websocket).
- Phase 5: generate_minutes_summary() (biên bản AI).
"""

import datetime
import os

from loguru import logger


def _load_minutes_keys_from_root_env():
    """Nạp SPEECHMATICS/GEMINI key từ root .env của repo nếu đang trống.

    bot.py gọi load_dotenv() nạp .env của example (key minutes để trống). Key thật
    nằm ở root repo (.env của pipecat) -> điền bổ sung mà không động tới key khác.
    """
    root_env = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    )
    if not os.path.exists(root_env):
        return
    try:
        from dotenv import dotenv_values

        vals = dotenv_values(root_env)
        for k in ("SPEECHMATICS_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
            if not os.getenv(k) and vals.get(k):
                os.environ[k] = vals[k]
    except Exception as e:  # pragma: no cover
        logger.warning(f"[minutes] Không nạp được key từ root .env: {e}")


_load_minutes_keys_from_root_env()

from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.speechmatics.stt import OperatingPoint, SpeechmaticsSTTService
from pipecat.transcriptions.language import Language

from streaming_recorder import StreamingAudioRecorder

# Ngôn ngữ cố định cho màn hình biên bản: tiếng Việt
MINUTES_LANGUAGE = Language.VI


# ---------------------------------------------------------------------------
# State global RIÊNG cho màn hình /minutes (không dùng chung với màn hình cũ)
# ---------------------------------------------------------------------------
minutes_recording_state = {
    "is_recording": False,
    "current_filename": None,
    "session_id": None,
}

# Instance recorder riêng (gán trong run_minutes_bot ở Phase 2)
minutes_audio_recorder_instance = None


def frame_to_message(frame) -> dict:
    """Chuyển một transcription frame -> message JSON gửi cho client.

    Đây là hàm thuần (pure) để dễ unit test. Speaker lấy từ ``frame.user_id``
    (Speechmatics gán nhãn S1/S2... vào đây khi bật diarization).
    """
    is_final = isinstance(frame, TranscriptionFrame)
    speaker = getattr(frame, "user_id", "") or ""
    language = getattr(frame, "language", None)
    return {
        "type": "transcription",
        "speaker": speaker,
        "text": frame.text,
        "timestamp": getattr(frame, "timestamp", None),
        "language": language.value if hasattr(language, "value") else language,
        "is_final": is_final,
    }


class SpeakerTranscriptBroadcaster(FrameProcessor):
    """Phát transcription (kèm nhãn người nói) tới các client WebSocket của /minutes.

    Khác với TranscriptBroadcaster của màn hình cũ: message có thêm field
    ``speaker`` và CHỈ lưu các câu final (is_final=True) vào lịch sử.
    """

    def __init__(self):
        super().__init__()
        self._websockets = []

    # -- quản lý websocket --------------------------------------------------
    def add_websocket(self, websocket):
        self._websockets.append(websocket)

    def remove_websocket(self, websocket):
        if websocket in self._websockets:
            self._websockets.remove(websocket)

    @property
    def websocket_count(self) -> int:
        return len(self._websockets)

    # -- xử lý frame --------------------------------------------------------
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            message = frame_to_message(frame)
            logger.info(f"📝 [{message['speaker'] or '?'}] {frame.text}")
            await self._broadcast(message)
        elif isinstance(frame, InterimTranscriptionFrame):
            message = frame_to_message(frame)
            await self._broadcast(message)

        await self.push_frame(frame, direction)

    # -- broadcast ----------------------------------------------------------
    async def _broadcast(self, message: dict):
        # Chỉ lưu lịch sử các câu final (interim thay đổi liên tục)
        if message.get("is_final"):
            self._save_to_history(message)

        if not self._websockets:
            logger.warning("⚠️ Chưa có client WebSocket nào kết nối /minutes")
            return

        disconnected = []
        for ws in self._websockets:
            try:
                await ws.send_json(message)
            except Exception as e:  # pragma: no cover - lỗi mạng runtime
                logger.warning(f"Gửi WebSocket thất bại: {e}")
                disconnected.append(ws)

        for ws in disconnected:
            self.remove_websocket(ws)

    def _save_to_history(self, message: dict):
        """Lưu câu final vào history service (Phase 4).

        Import lazy + try/except để Phase 1 chạy độc lập trước khi Phase 4 tồn tại.
        """
        try:
            from minutes_history_service import minutes_history_service
        except ImportError:
            return
        try:
            minutes_history_service.save_transcript(message)
        except Exception as e:  # pragma: no cover
            logger.warning(f"Lưu transcript thất bại: {e}")


# Broadcaster global RIÊNG cho /minutes
minutes_transcript_broadcaster = SpeakerTranscriptBroadcaster()


# ---------------------------------------------------------------------------
# Phase 2 — Pipeline Speechmatics (diarization tiếng Việt)
# ---------------------------------------------------------------------------
def _env_int(name: str):
    """Đọc env -> int, trả None nếu trống/không hợp lệ (để Speechmatics tự dò)."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning(f"{name}={raw!r} không phải số nguyên, bỏ qua.")
        return None


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(f"{name}={raw!r} không phải số thực, dùng mặc định {default}.")
        return default


def build_minutes_stt_params():
    """Cấu hình InputParams cho Speechmatics (tách ra để dễ unit test).

    Lưu ý: ``speaker_active_format="{text}"`` để text giữ NGUYÊN (không nhúng nhãn),
    còn nhãn người nói (S1/S2...) nằm ở ``TranscriptionFrame.user_id`` -> frontend
    gom nhóm theo speaker. Operating point mặc định ENHANCED (chính xác cao);
    đặt MINUTES_OPERATING_POINT=standard nếu cần latency thấp hơn.
    """
    op_raw = os.getenv("MINUTES_OPERATING_POINT", "enhanced").strip().lower()
    operating_point = (
        OperatingPoint.STANDARD if op_raw == "standard" else OperatingPoint.ENHANCED
    )
    return SpeechmaticsSTTService.InputParams(
        language=MINUTES_LANGUAGE,
        operating_point=operating_point,
        enable_diarization=True,
        speaker_sensitivity=_env_float("MINUTES_SPEAKER_SENSITIVITY", 0.5),
        max_speakers=_env_int("MINUTES_MAX_SPEAKERS"),
        speaker_active_format="{text}",
        speaker_passive_format="{text}",
    )


def build_speechmatics_stt(api_key: str = None) -> SpeechmaticsSTTService:
    """Tạo Speechmatics STT cho cuộc họp tiếng Việt nhiều người (diarization)."""
    return SpeechmaticsSTTService(
        api_key=api_key or os.getenv("SPEECHMATICS_API_KEY"),
        params=build_minutes_stt_params(),
    )


def build_minutes_pipeline(transport, stt, recorder, broadcaster=None) -> Pipeline:
    """Dựng pipeline /minutes: input -> STT(diarization) -> broadcaster -> recorder."""
    broadcaster = broadcaster or minutes_transcript_broadcaster
    return Pipeline(
        [
            transport.input(),  # Audio vào
            stt,  # Speech-to-text + tách người nói
            broadcaster,  # Phát transcript (kèm speaker) tới WebSocket
            recorder,  # Ghi âm ra file WAV
        ]
    )


def _new_minutes_filename() -> str:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"minutes_vi_{ts}.wav"


async def run_minutes_bot(transport, runner_args):
    """Chạy bot màn hình /minutes (pipeline Speechmatics riêng)."""
    global minutes_audio_recorder_instance

    logger.info("Starting Meeting Minutes (diarization) Bot")

    stt = build_speechmatics_stt()
    recorder = StreamingAudioRecorder(output_dir="recordings")
    minutes_audio_recorder_instance = recorder

    pipeline = build_minutes_pipeline(transport, stt, recorder)

    idle = getattr(runner_args, "pipeline_idle_timeout_secs", 0) or 0
    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        idle_timeout_secs=idle if idle > 0 else 3600,
        cancel_on_idle_timeout=False,
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("✅ [minutes] Client connected - bắt đầu nhận audio")
        if minutes_recording_state["is_recording"] and minutes_audio_recorder_instance:
            filename = minutes_recording_state.get("current_filename") or _new_minutes_filename()
            minutes_recording_state["current_filename"] = filename
            await minutes_audio_recorder_instance.start_recording(filename)
            logger.info(f"🎙️ [minutes] Ghi âm vào {filename}")

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("[minutes] Client disconnected")
        if minutes_audio_recorder_instance:
            await minutes_audio_recorder_instance.stop_recording()
        # KHÔNG cancel task để giữ pipeline cho lần kết nối sau (giống màn hình cũ)

    runner = PipelineRunner(handle_sigint=getattr(runner_args, "handle_sigint", False))
    await runner.run(task)


async def minutes_bot_entry(runner_args):
    """Entry point tạo transport rồi chạy bot /minutes (dùng cho offer callback)."""
    from pipecat.runner.utils import create_transport
    from minutes_transport import minutes_transport_params

    transport = await create_transport(runner_args, minutes_transport_params)
    await run_minutes_bot(transport, runner_args)
