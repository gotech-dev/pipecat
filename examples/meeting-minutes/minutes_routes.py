#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Routes & WebSocket cho màn hình /minutes (tách biệt hoàn toàn với /meeting).

Đăng ký qua setup_minutes_routes(app, small_webrtc_handler). Dùng CHUNG
small_webrtc_handler với màn hình cũ nhưng có offer endpoint riêng để chạy
pipeline Speechmatics (run_minutes_bot) thay vì pipeline Gladia.
"""

import asyncio
import datetime
import os

from fastapi import (
    BackgroundTasks,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, RedirectResponse, Response
from loguru import logger

import minutes_bot
from minutes_history_service import minutes_history_service
from minutes_summary import generate_minutes_summary

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def _check_auth(request: Request):
    """Chặn nếu chưa đăng nhập (dùng chung session với màn hình cũ)."""
    if not request.session.get("authenticated", False):
        raise HTTPException(status_code=401, detail="Unauthorized")


# Khoảng "grace" giữ session mở thêm sau khi bấm Stop, để các câu final MUỘN của
# Speechmatics (ENHANCED + diarization trả final trễ vài giây) kịp lưu vào history
# trước khi đóng session + tóm tắt. Nếu đóng ngay -> đuôi cuộc họp bị rớt khỏi
# transcript -> biên bản thiếu ý. Chỉnh qua env MINUTES_STOP_GRACE_SECS.
DEFAULT_STOP_GRACE_SECS = 4.0


def _stop_grace_secs() -> float:
    """Đọc MINUTES_STOP_GRACE_SECS (giây). Trả mặc định nếu trống/không hợp lệ/âm."""
    raw = os.getenv("MINUTES_STOP_GRACE_SECS", "").strip()
    if not raw:
        return DEFAULT_STOP_GRACE_SECS
    try:
        val = float(raw)
    except ValueError:
        logger.warning(
            f"MINUTES_STOP_GRACE_SECS={raw!r} không phải số, dùng {DEFAULT_STOP_GRACE_SECS}"
        )
        return DEFAULT_STOP_GRACE_SECS
    return val if val >= 0 else DEFAULT_STOP_GRACE_SECS


def run_summary_for_session(session_id: str):
    """Sinh biên bản AI cho session (chạy nền). Lỗi không làm hỏng luồng dừng ghi."""
    try:
        summary = generate_minutes_summary(session_id)
        if summary:
            logger.info(f"📝 [minutes] Đã sinh biên bản cho {session_id}")
        else:
            logger.warning(f"⚠️ [minutes] Không sinh được biên bản cho {session_id}")
    except Exception as e:  # pragma: no cover
        logger.error(f"❌ [minutes] Lỗi sinh biên bản: {e}", exc_info=True)


async def finalize_and_summarize(session_id: str):
    """Đóng-trễ session rồi sinh biên bản (chạy nền sau khi đã trả response Stop).

    1) Chờ ``grace`` giây để các câu final muộn của STT kịp ``save_transcript`` vào
       session (broadcaster vẫn đang chạy vì pipeline không bị cancel khi Stop).
    2) Đóng đúng session này (guard ``expected_session_id``) -> ghi JSON đầy đủ.
    3) Gọi LLM (blocking I/O) trong thread riêng để không chẹn event loop.
    """
    grace = _stop_grace_secs()
    if grace > 0:
        try:
            await asyncio.sleep(grace)
        except asyncio.CancelledError:  # pragma: no cover - shutdown
            logger.warning(f"[minutes] Grace wait bị huỷ cho {session_id}")

    json_path = minutes_history_service.end_session(expected_session_id=session_id)
    if json_path is None:
        # Session đã bị thay thế bởi phiên mới (hiếm) -> vẫn thử tóm tắt nếu JSON cũ tồn tại
        logger.info(f"[minutes] Đóng-trễ {session_id} không tạo JSON mới, thử tóm tắt JSON sẵn có")

    await asyncio.to_thread(run_summary_for_session, session_id)


def _new_session_id() -> str:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"minutes_vi_{ts}"


def _format_session_datetime(session_id: str) -> str:
    """minutes_vi_YYYYMMDD_HHMMSS -> 'DD/MM/YYYY HH:MM:SS' (rỗng nếu không parse được)."""
    parts = session_id.split("_")  # ['minutes', 'vi', 'YYYYMMDD', 'HHMMSS']
    d = parts[2] if len(parts) > 2 else ""
    t = parts[3] if len(parts) > 3 else ""
    date_str = f"{d[6:8]}/{d[4:6]}/{d[0:4]}" if len(d) == 8 else d
    time_str = f"{t[0:2]}:{t[2:4]}:{t[4:6]}" if len(t) == 6 else t
    return f"{date_str} {time_str}".strip()


def build_minutes_markdown(session_id: str, summary: str) -> str:
    """Bọc summary (đã là Markdown) thành file biên bản có tiêu đề + ngày giờ."""
    when = _format_session_datetime(session_id)
    header = "# Biên bản cuộc họp\n"
    if when:
        header += f"\n*{when}*\n"
    return f"{header}\n---\n\n{summary.strip()}\n"


def setup_minutes_routes(app, small_webrtc_handler=None):
    """Đăng ký toàn bộ route của màn hình /minutes vào FastAPI app."""

    # ----------------------------------------------------------- WebRTC offer
    @app.post("/api/minutes/offer")
    async def minutes_offer(request: Request, background_tasks: BackgroundTasks):
        if small_webrtc_handler is None:
            raise HTTPException(status_code=503, detail="WebRTC handler chưa sẵn sàng")

        # import lazy để không cần extra webrtc khi chỉ chạy unit test
        from pipecat.runner.types import SmallWebRTCRunnerArguments
        from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequest

        try:
            data = await request.json()
            webrtc_request = SmallWebRTCRequest.from_dict(data)

            async def webrtc_connection_callback(connection):
                runner_args = SmallWebRTCRunnerArguments(
                    webrtc_connection=connection, body=webrtc_request.request_data
                )
                background_tasks.add_task(minutes_bot.minutes_bot_entry, runner_args)

            answer = await small_webrtc_handler.handle_web_request(
                request=webrtc_request,
                webrtc_connection_callback=webrtc_connection_callback,
            )
            logger.info(f"[minutes] WebRTC offer accepted, pc_id: {answer.get('pc_id')}")
            return answer
        except Exception as e:
            logger.error(f"[minutes] Lỗi xử lý WebRTC offer: {e}", exc_info=True)
            raise HTTPException(status_code=422, detail=f"Invalid WebRTC request: {e}")

    # ----------------------------------------------------------- start/stop
    @app.post("/api/minutes/start-recording")
    async def minutes_start_recording(request: Request):
        _check_auth(request)
        session_id = _new_session_id()
        filename = f"{session_id}.wav"

        # Engine STT do frontend chọn: "premium" (mặc định) | "internal".
        # Body có thể trống (tương thích ngược) -> mặc định premium.
        engine = "premium"
        try:
            body = await request.json()
            if isinstance(body, dict) and body.get("engine") == "internal":
                engine = "internal"
        except Exception:
            pass

        minutes_bot.minutes_recording_state["is_recording"] = True
        minutes_bot.minutes_recording_state["current_filename"] = filename
        minutes_bot.minutes_recording_state["session_id"] = session_id
        minutes_bot.minutes_recording_state["engine"] = engine

        minutes_history_service.start_session(session_id)
        logger.info(f"🎬 [minutes] Bắt đầu ghi: {filename} (engine={engine})")
        return {
            "status": "recording",
            "filename": filename,
            "session_id": session_id,
            "engine": engine,
        }

    @app.post("/api/minutes/stop-recording")
    async def minutes_stop_recording(request: Request, background_tasks: BackgroundTasks):
        _check_auth(request)
        minutes_bot.minutes_recording_state["is_recording"] = False

        if minutes_bot.minutes_audio_recorder_instance:
            await minutes_bot.minutes_audio_recorder_instance.stop_recording()

        session_id = minutes_bot.minutes_recording_state.get("session_id")
        minutes_bot.minutes_recording_state["current_filename"] = None
        minutes_bot.minutes_recording_state["session_id"] = None

        # KHÔNG đóng session ngay: giữ mở thêm 'grace window' để các câu final muộn
        # của Speechmatics kịp lưu, rồi mới đóng + sinh biên bản (tránh thiếu ý ở
        # đoạn cuối). Toàn bộ chạy nền sau khi response đã trả về.
        if session_id:
            background_tasks.add_task(finalize_and_summarize, session_id)
        else:
            # Không có session_id (bất thường) -> vẫn đóng để không rò rỉ session
            minutes_history_service.end_session()

        logger.info(f"🛑 [minutes] Dừng ghi: {session_id} (đóng-trễ {_stop_grace_secs()}s)")
        return {"status": "stopped", "session_id": session_id}

    @app.get("/api/minutes/status")
    async def minutes_status(request: Request):
        _check_auth(request)
        return {
            "is_recording": minutes_bot.minutes_recording_state["is_recording"],
            "language": "vi",
        }

    # ----------------------------------------------------------- summary/history
    @app.get("/api/minutes/summary/{session_id}")
    async def minutes_get_summary(request: Request, session_id: str):
        _check_auth(request)
        return {
            "session_id": session_id,
            "summary": minutes_history_service.get_summary(session_id),
        }

    @app.get("/api/minutes/summary/{session_id}/download")
    async def minutes_download_summary(request: Request, session_id: str):
        _check_auth(request)
        summary = minutes_history_service.get_summary(session_id)
        if not summary:
            raise HTTPException(status_code=404, detail="Chưa có biên bản cho phiên này")
        content = build_minutes_markdown(session_id, summary)
        filename = f"bien-ban-{session_id}.md"
        return Response(
            content=content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/minutes/history")
    async def minutes_history(request: Request):
        _check_auth(request)
        return minutes_history_service.get_all_recordings()

    @app.get("/api/minutes/history/{recording_id}")
    async def minutes_history_detail(request: Request, recording_id: str):
        _check_auth(request)
        detail = minutes_history_service.get_recording_detail(recording_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy bản ghi")
        return detail

    # ----------------------------------------------------------- WebSocket
    @app.websocket("/ws/minutes-transcripts")
    async def minutes_ws(websocket: WebSocket):
        await websocket.accept()
        minutes_bot.minutes_transcript_broadcaster.add_websocket(websocket)
        logger.info("🔌 [minutes] WebSocket client kết nối")
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            minutes_bot.minutes_transcript_broadcaster.remove_websocket(websocket)
            logger.info("🔌 [minutes] WebSocket client ngắt kết nối")

    # ----------------------------------------------------------- page
    @app.get("/minutes")
    async def minutes_page(request: Request):
        if not request.session.get("authenticated", False):
            return RedirectResponse(url="/", status_code=302)
        html_path = os.path.join(STATIC_DIR, "minutes.html")
        if not os.path.exists(html_path):
            raise HTTPException(status_code=404, detail="minutes.html chưa tồn tại")
        return FileResponse(html_path)

    @app.get("/minutes/history")
    async def minutes_history_page(request: Request):
        if not request.session.get("authenticated", False):
            return RedirectResponse(url="/", status_code=302)
        html_path = os.path.join(STATIC_DIR, "minutes_history.html")
        if not os.path.exists(html_path):
            raise HTTPException(status_code=404, detail="minutes_history.html chưa tồn tại")
        return FileResponse(html_path)

    logger.info("✅ [minutes] Đã đăng ký routes màn hình /minutes")
