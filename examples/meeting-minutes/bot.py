# -*- coding: utf-8 -*-
#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Meeting Minutes Bot - Ghi âm và dịch realtime cuộc họp.

Chức năng:
- Thu âm cuộc họp (30 phút - 1.5 giờ)
- Speech-to-Text realtime (Tiếng Nhật hoặc Tiếng Anh)
- Dịch sang Tiếng Việt realtime
- Hiển thị trên web UI

Chạy::
    uv run bot.py -t webrtc
"""

import asyncio
import datetime
import os
from typing import Optional

from dotenv import load_dotenv
try:
    from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles
except ImportError:
    # FastAPI not available, will be imported when needed
    FastAPI = None
    Request = None
    WebSocket = None
    WebSocketDisconnect = None
    HTMLResponse = None
    StaticFiles = None
from loguru import logger

from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import Frame, InterimTranscriptionFrame, TranscriptionFrame, TranslationFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.gladia.config import (
    GladiaInputParams,
    LanguageConfig,
    RealtimeProcessingConfig,
    TranslationConfig,
)
from pipecat.services.gladia.stt import GladiaSTTService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams

# Optional Daily import (only needed if using Daily transport)
# We need to catch Exception because the daily transport module itself raises
# an exception if the daily package is not installed
DailyParams = None
try:
    import sys
    # Check if daily package exists before importing the transport module
    import importlib.util
    spec = importlib.util.find_spec("daily")
    if spec is not None:
        from pipecat.transports.daily.transport import DailyParams
except (ImportError, Exception) as e:
    # Daily package not installed, that's fine - we only use WebRTC
    DailyParams = None

# Import streaming recorder
from streaming_recorder import StreamingAudioRecorder

# Import history service
from history_service import history_service

load_dotenv(override=True)

# Global state for recording and language
recording_state = {"is_recording": False, "language": Language.JA, "websockets": [], "current_filename": None}
audio_recorder_instance = None


class TranscriptBroadcaster(FrameProcessor):
    """Broadcasts transcription and translation frames to connected WebSocket clients."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._websockets = []

    def add_websocket(self, websocket: WebSocket):
        """Add a WebSocket client to broadcast to."""
        self._websockets.append(websocket)

    def remove_websocket(self, websocket: WebSocket):
        """Remove a WebSocket client."""
        if websocket in self._websockets:
            self._websockets.remove(websocket)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process frames and broadcast transcriptions/translations."""
        await super().process_frame(frame, direction)

        # Handle final transcription
        if isinstance(frame, TranscriptionFrame):
            logger.info(f"📝 Transcription ({frame.language}): {frame.text}")
            message = {
                "type": "transcription",
                "language": frame.language,
                "text": frame.text,
                "timestamp": frame.timestamp,
                "is_final": True,
            }
            await self._broadcast(message)

        # Handle interim transcription (realtime partial results)
        elif isinstance(frame, InterimTranscriptionFrame):
            logger.info(f"📝 Interim ({frame.language}): {frame.text}")
            message = {
                "type": "transcription",
                "language": frame.language,
                "text": frame.text,
                "timestamp": frame.timestamp,
                "is_final": False,
            }
            await self._broadcast(message)

        # Handle translation
        elif isinstance(frame, TranslationFrame):
            logger.info(f"🌐 Translation ({frame.language}): {frame.text}")
            message = {
                "type": "translation",
                "language": frame.language,
                "text": frame.text,
                "timestamp": frame.timestamp,
            }
            await self._broadcast(message)

        await self.push_frame(frame, direction)

    async def _broadcast(self, message: dict):
        """Broadcast message to all connected WebSocket clients."""
        # Save to history service
        if message.get("type") == "transcription":
            history_service.save_transcript(message)
            logger.debug(f"💾 Attempted to save transcript: is_final={message.get('is_final')}, text={message.get('text', '')[:30]}...")
        elif message.get("type") == "translation":
            history_service.save_translation(message)
            logger.debug(f"💾 Attempted to save translation: text={message.get('text', '')[:30]}...")
        
        if not self._websockets:
            logger.warning("⚠️ No WebSocket clients connected to receive transcript")
            return
            
        disconnected = []
        for ws in self._websockets:
            try:
                await ws.send_json(message)
                logger.debug(f"✅ Sent message to WebSocket: {message.get('type')}")
            except Exception as e:
                logger.warning(f"Failed to send to WebSocket: {e}")
                disconnected.append(ws)

        # Remove disconnected clients
        for ws in disconnected:
            self.remove_websocket(ws)


# Global transcript broadcaster
transcript_broadcaster = TranscriptBroadcaster()

# Transport parameters
transport_params = {
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=False,  # No audio output needed
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
        turn_analyzer=LocalSmartTurnAnalyzerV3(params=SmartTurnParams()),
    ),
    "twilio": lambda: FastAPIWebsocketParams(
        audio_in_enabled=True,
        audio_out_enabled=False,
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
        turn_analyzer=LocalSmartTurnAnalyzerV3(params=SmartTurnParams()),
    ),
}

# Add Daily transport only if available
if DailyParams is not None:
    transport_params["daily"] = lambda: DailyParams(
        audio_in_enabled=True,
        audio_out_enabled=False,  # No audio output needed
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
        turn_analyzer=LocalSmartTurnAnalyzerV3(params=SmartTurnParams()),
    )


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    """Main bot function."""
    global audio_recorder_instance
    
    logger.info("Starting Meeting Minutes Bot")

    # Get current language from global state
    input_language = recording_state["language"]

    # Initialize Gladia STT with translation
    stt = GladiaSTTService(
        api_key=os.getenv("GLADIA_API_KEY"),
        region=os.getenv("GLADIA_REGION", "us-west"),
        params=GladiaInputParams(
            language_config=LanguageConfig(
                languages=[input_language],  # Japanese or English
                code_switching=False,
            ),
            realtime_processing=RealtimeProcessingConfig(
                translation=True,  # Enable translation
                translation_config=TranslationConfig(
                    target_languages=[Language.VI],  # Translate to Vietnamese
                    model="enhanced",  # Use enhanced translation model
                ),
            ),
        ),
    )

    # Initialize streaming audio recorder
    audio_recorder = StreamingAudioRecorder(output_dir="recordings")
    audio_recorder_instance = audio_recorder

    # Create pipeline
    pipeline = Pipeline(
        [
            transport.input(),  # Audio input
            stt,  # Speech-to-text + translation
            transcript_broadcaster,  # Broadcast to WebSocket clients
            audio_recorder,  # Record to file
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("✅ WebRTC Client connected - Audio input ready")
        # Start recording if requested - use the filename from recording_state
        if recording_state["is_recording"] and audio_recorder_instance:
            filename = recording_state.get("current_filename")
            if filename:
                await audio_recorder_instance.start_recording(filename)
                logger.info(f"🎙️ Started recording to {filename}")
            else:
                # Fallback: generate new filename if not set (shouldn't happen)
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                lang_code = "ja" if input_language == Language.JA else "en"
                filename = f"meeting_{lang_code}_{timestamp}.wav"
                await audio_recorder_instance.start_recording(filename)
                # Also start history session with this filename
                session_id = filename.replace(".wav", "")
                history_service.start_session(session_id)
                recording_state["current_filename"] = filename
                logger.info(f"🎙️ Started recording to {filename} (fallback)")

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        if audio_recorder_instance:
            await audio_recorder_instance.stop_recording()
        await task.cancel()

    runner = PipelineRunner(handle_sigint=runner_args.handle_sigint)
    await runner.run(task)


async def bot(runner_args: RunnerArguments):
    """Main bot entry point."""
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


# Function to setup custom routes (called by runner)
def setup_custom_routes(app: FastAPI):
    """Setup custom API routes for meeting minutes."""
    global audio_recorder_instance
    
    # Override /api/offer endpoint to handle dict parsing (FastAPI may not parse dataclass)
    from fastapi import Request as FastAPIRequest, BackgroundTasks, APIRouter
    from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequest, SmallWebRTCRequestHandler
    from pipecat.runner.types import SmallWebRTCRunnerArguments
    from pipecat.runner.run import _get_bot_module
    
    logger.info("Setting up custom routes...")
    
    # 1. Find existing routes and extract handler from PATCH route
    small_webrtc_handler = None
    patch_route_found = False
    post_route_to_remove = None
    
    for route in app.routes:
        if hasattr(route, 'path') and route.path == '/api/offer':
            # Check for PATCH route to get handler
            if hasattr(route, 'methods') and 'PATCH' in route.methods:
                patch_route_found = True
                # Try to extract handler from closure
                if hasattr(route.endpoint, '__closure__') and route.endpoint.__closure__:
                    for cell in route.endpoint.__closure__:
                        try:
                            cell_value = cell.cell_contents
                            if isinstance(cell_value, SmallWebRTCRequestHandler):
                                small_webrtc_handler = cell_value
                                logger.info(f"✅ Found handler in PATCH route: {id(small_webrtc_handler)}")
                                break
                        except (ValueError, AttributeError, TypeError):
                            continue
            
            # Check for POST route to remove
            if hasattr(route, 'methods') and 'POST' in route.methods:
                post_route_to_remove = route

    # Fallback: check app state if closure extraction failed
    if not small_webrtc_handler:
        small_webrtc_handler = getattr(app, '_small_webrtc_handler', None)
        if small_webrtc_handler:
             logger.info(f"✅ Found handler in app state: {id(small_webrtc_handler)}")

    if not small_webrtc_handler:
        logger.error("❌ Could not find SmallWebRTCRequestHandler! WebRTC will not work.")
        # We can't really proceed without the handler if we want to use the existing PATCH route
        # But we'll create one just to prevent crash, though connections won't match
        small_webrtc_handler = SmallWebRTCRequestHandler()

    # 2. Remove ONLY the existing POST route
    if post_route_to_remove:
        app.routes.remove(post_route_to_remove)
        logger.info("🗑️ Removed existing POST /api/offer route")
    else:
        logger.warning("⚠️ Could not find existing POST /api/offer route to remove")

    # 3. Add new POST route
    @app.post("/api/offer")
    async def offer_endpoint(request: FastAPIRequest, background_tasks: BackgroundTasks):
        """Handle WebRTC offer with proper dict parsing."""
        try:
            data = await request.json()
            logger.debug(f"Received WebRTC offer: {data.keys()}")
            
            # Convert dict to SmallWebRTCRequest using from_dict method
            webrtc_request = SmallWebRTCRequest.from_dict(data)
            
            # Prepare callback to run bot
            async def webrtc_connection_callback(connection):
                bot_module = _get_bot_module()
                runner_args = SmallWebRTCRunnerArguments(
                    webrtc_connection=connection, body=webrtc_request.request_data
                )
                background_tasks.add_task(bot_module.bot, runner_args)
            
            # Handle the request using the SHARED handler
            answer = await small_webrtc_handler.handle_web_request(
                request=webrtc_request,
                webrtc_connection_callback=webrtc_connection_callback,
            )
            
            logger.info(f"WebRTC offer accepted, pc_id: {answer.get('pc_id')}")
            return answer
            
        except Exception as e:
            logger.error(f"Error handling WebRTC offer: {e}", exc_info=True)
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail=f"Invalid WebRTC request: {str(e)}")
    
    logger.info("✅ Registered custom POST /api/offer route")
    
    # Note: We do NOT touch the PATCH route. It should continue to work with the shared handler.

    # Log active routes for debugging
    logger.info("--- Active /api/offer Routes ---")
    for route in app.routes:
        if hasattr(route, 'path') and '/api/offer' in route.path:
            logger.info(f"Route: {route.path} {route.methods}")
    logger.info("--------------------------------")
    
    @app.post("/api/start-recording")
    async def start_recording(request: Request):
        """Start recording with specified language."""
        data = await request.json()
        language_code = data.get("language", "ja")  # Default to Japanese

        # Map language code to Language enum
        language_map = {
            "ja": Language.JA,
            "en": Language.EN,
        }
        language = language_map.get(language_code, Language.JA)

        recording_state["language"] = language
        recording_state["is_recording"] = True

        # Generate filename and start history session first
        # IMPORTANT: Use same timestamp for both JSON and WAV files
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"meeting_{language_code}_{timestamp}.wav"
        session_id = filename.replace(".wav", "")
        
        # Store filename in global state so on_client_connected can use it
        recording_state["current_filename"] = filename
        
        # Start history session (always, even if audio recorder not ready yet)
        history_service.start_session(session_id)
        logger.info(f"📚 Started history session: {session_id}")

        # Start recording if audio recorder is ready
        if audio_recorder_instance:
            await audio_recorder_instance.start_recording(filename)
        else:
            logger.warning("⚠️ Audio recorder not ready, but history session started")

        logger.info(f"Recording started with language: {language_code}")

        return {
            "status": "recording",
            "language": language_code,
        }

    @app.post("/api/stop-recording")
    async def stop_recording():
        """Stop recording."""
        recording_state["is_recording"] = False
        if audio_recorder_instance:
            await audio_recorder_instance.stop_recording()
        # End history session and save to JSON (always, even if recorder not ready)
        history_service.end_session()
        # Clear current filename
        recording_state["current_filename"] = None
        logger.info("Recording stopped")
        return {"status": "stopped"}

    @app.get("/api/status")
    async def get_status():
        """Get current recording status."""
        return {
            "is_recording": recording_state["is_recording"],
            "language": recording_state["language"].value if recording_state["language"] else None,
        }

    @app.websocket("/ws/transcripts")
    async def websocket_transcripts(websocket: WebSocket):
        """WebSocket endpoint for receiving real-time transcripts."""
        await websocket.accept()
        transcript_broadcaster.add_websocket(websocket)
        logger.info("WebSocket client connected for transcripts")

        try:
            while True:
                # Keep connection alive
                await websocket.receive_text()
        except WebSocketDisconnect:
            logger.info("WebSocket client disconnected")
        finally:
            transcript_broadcaster.remove_websocket(websocket)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def get_index():
        """Serve the main HTML page."""
        html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
        if os.path.exists(html_path):
            with open(html_path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
        return HTMLResponse(content="<h1>Meeting Minutes Bot</h1><p>UI file not found</p>")
    
    @app.get("/meeting", response_class=HTMLResponse, include_in_schema=False)
    async def get_meeting_ui():
        """Alternative route for meeting UI."""
        html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
        if os.path.exists(html_path):
            with open(html_path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
        return HTMLResponse(content="<h1>Meeting Minutes Bot</h1><p>UI file not found</p>")

    # Mount static files
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")
    
    # Setup history routes
    from history_routes import setup_history_routes
    setup_history_routes(app)


if __name__ == "__main__":
    from pipecat.runner.run import main
    
    # Monkey patch to add our custom routes
    try:
        from pipecat.runner.run import _setup_webrtc_routes
        from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequestHandler
        
        original_setup_webrtc = _setup_webrtc_routes
        
        def setup_webrtc_with_custom_routes(app, *args, **kwargs):
            # First setup WebRTC routes
            original_setup_webrtc(app, *args, **kwargs)
            
            # Extract handler from the PATCH endpoint (it uses the same handler)
            # The handler is created in _setup_webrtc_routes and used in both POST and PATCH
            handler_found = False
            for route in app.routes:
                if hasattr(route, 'path') and route.path == '/api/offer':
                    if hasattr(route, 'methods'):
                        # Check both POST and PATCH endpoints
                        if 'POST' in route.methods or 'PATCH' in route.methods:
                            # Try to extract handler from closure
                            if hasattr(route.endpoint, '__closure__') and route.endpoint.__closure__:
                                for cell in route.endpoint.__closure__:
                                    try:
                                        cell_value = cell.cell_contents
                                        if isinstance(cell_value, SmallWebRTCRequestHandler):
                                            if not hasattr(app, '_small_webrtc_handler') or app._small_webrtc_handler != cell_value:
                                                app._small_webrtc_handler = cell_value
                                                handler_found = True
                                                logger.info(f"✅ Found and stored SmallWebRTCRequestHandler from {route.methods}: {id(cell_value)}")
                                                break
                                    except (ValueError, AttributeError, TypeError):
                                        continue
                            if handler_found:
                                break
                if handler_found:
                    break
            
            if not handler_found:
                logger.error("❌ Could not extract handler from closure - ICE candidates will fail!")
                logger.error("This is a critical error. The handler must be shared between POST and PATCH endpoints.")
            
            # Then add our custom routes (which will override /api/offer to parse dict)
            setup_custom_routes(app)
        
        # Replace the function
        import pipecat.runner.run as runner_module
        runner_module._setup_webrtc_routes = setup_webrtc_with_custom_routes
        logger.info("Custom routes will be added to WebRTC app")
    except Exception as e:
        logger.warning(f"Could not setup custom routes: {e}")
    
    main()

