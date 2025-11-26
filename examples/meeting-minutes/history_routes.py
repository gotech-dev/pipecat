#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""API routes for meeting history functionality."""

import os
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from history_service import history_service


def setup_history_routes(app: FastAPI):
    """Setup history-related API routes.

    Args:
        app: FastAPI application instance.
    """
    logger.info("Setting up history routes...")

    @app.get("/api/history")
    async def get_history(request: Request):
        """Get list of all recordings with metadata."""
        # Check authentication
        if not request.session.get("authenticated", False):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        try:
            recordings = history_service.get_all_recordings()
            return {"recordings": recordings}
        except Exception as e:
            logger.error(f"Error getting history: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/history/{recording_id}")
    async def get_recording_detail(recording_id: str, request: Request):
        """Get detailed transcript and translation data for a recording.

        Args:
            recording_id: The recording ID (e.g., "meeting_ja_20251126_211110").
        """
        # Check authentication
        if not request.session.get("authenticated", False):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        try:
            detail = history_service.get_recording_detail(recording_id)
            if detail is None:
                raise HTTPException(status_code=404, detail="Recording not found")
            return detail
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting recording detail: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/history", response_class=HTMLResponse, include_in_schema=False)
    async def get_history_page(request: Request):
        """Serve the history HTML page."""
        # Check authentication
        if not request.session.get("authenticated", False):
            return RedirectResponse(url="/", status_code=302)
        
        html_path = os.path.join(os.path.dirname(__file__), "static", "history.html")
        if os.path.exists(html_path):
            with open(html_path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
        return HTMLResponse(content="<h1>History Page</h1><p>History UI file not found</p>")

    # Mount recordings folder to serve WAV files (only if not already mounted)
    recordings_dir = os.path.join(os.path.dirname(__file__), "recordings")
    if os.path.exists(recordings_dir):
        # Check if /recordings is already mounted
        already_mounted = any(
            hasattr(route, 'path') and route.path == '/recordings'
            for route in app.routes
        )
        if not already_mounted:
            app.mount("/recordings", StaticFiles(directory=recordings_dir), name="recordings")
            logger.info(f"✅ Mounted /recordings to serve audio files")
        else:
            logger.info(f"ℹ️ /recordings already mounted, skipping")

    logger.info("✅ History routes setup complete")

