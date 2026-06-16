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

import datetime
import os

from fastapi import (
    BackgroundTasks,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, RedirectResponse
from loguru import logger

import minutes_bot
from minutes_history_service import minutes_history_service
from minutes_summary import generate_minutes_summary

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def _check_auth(request: Request):
    """Chặn nếu chưa đăng nhập (dùng chung session với màn hình cũ)."""
    if not request.session.get("authenticated", False):
        raise HTTPException(status_code=401, detail="Unauthorized")


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


def _new_session_id() -> str:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"minutes_vi_{ts}"


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

        minutes_bot.minutes_recording_state["is_recording"] = True
        minutes_bot.minutes_recording_state["current_filename"] = filename
        minutes_bot.minutes_recording_state["session_id"] = session_id

        minutes_history_service.start_session(session_id)
        logger.info(f"🎬 [minutes] Bắt đầu ghi: {filename}")
        return {"status": "recording", "filename": filename, "session_id": session_id}

    @app.post("/api/minutes/stop-recording")
    async def minutes_stop_recording(request: Request, background_tasks: BackgroundTasks):
        _check_auth(request)
        minutes_bot.minutes_recording_state["is_recording"] = False

        if minutes_bot.minutes_audio_recorder_instance:
            await minutes_bot.minutes_audio_recorder_instance.stop_recording()

        session_id = minutes_bot.minutes_recording_state.get("session_id")
        minutes_history_service.end_session()
        minutes_bot.minutes_recording_state["current_filename"] = None
        minutes_bot.minutes_recording_state["session_id"] = None

        # Sinh biên bản AI ở nền (Starlette chạy hàm sync trong threadpool -> không block)
        if session_id:
            background_tasks.add_task(run_summary_for_session, session_id)

        logger.info(f"🛑 [minutes] Dừng ghi: {session_id}")
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
