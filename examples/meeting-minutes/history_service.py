#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""History service for saving and retrieving meeting transcripts and translations."""

import json
import os
from typing import Optional, Dict, List
from loguru import logger


class HistoryService:
    """Service to save and retrieve meeting history (transcripts and translations)."""

    def __init__(self, recordings_dir: str = "recordings"):
        """Initialize the history service.

        Args:
            recordings_dir: Directory where recordings and JSON files are stored.
        """
        self._recordings_dir = recordings_dir
        os.makedirs(recordings_dir, exist_ok=True)

    def start_session(self, session_id: str):
        """Start a new recording session.

        Args:
            session_id: Unique identifier for the session (e.g., "meeting_ja_20251126_211110").
        """
        self._current_session_id = session_id
        self._session_data = {
            "session_id": session_id,
            "transcripts": [],
            "translations": [],
        }
        logger.info(f"📚 Started history session: {session_id}")

    def save_transcript(self, message: dict):
        """Save a transcription message to the current session.

        Args:
            message: Dictionary with type="transcription", language, text, timestamp, etc.
        """
        if not hasattr(self, "_current_session_id") or not self._current_session_id:
            logger.warning("⚠️ No active session to save transcript")
            return  # No active session

        if not hasattr(self, "_session_data"):
            logger.warning("⚠️ Session data not initialized")
            return

        # Save both final and interim transcriptions
        # Final transcriptions are more reliable, but interim can be useful too
        self._session_data["transcripts"].append(message)
        is_final = message.get("is_final", False)
        logger.debug(f"💾 Saved {'final' if is_final else 'interim'} transcript: {message.get('text', '')[:50]}...")

    def save_translation(self, message: dict):
        """Save a translation message to the current session.

        Args:
            message: Dictionary with type="translation", language, text, timestamp, etc.
        """
        if not hasattr(self, "_current_session_id") or not self._current_session_id:
            return  # No active session

        self._session_data["translations"].append(message)
        logger.debug(f"💾 Saved translation: {message.get('text', '')[:50]}...")

    def end_session(self):
        """End the current session and save to JSON file."""
        if not hasattr(self, "_current_session_id") or not self._current_session_id:
            logger.warning("⚠️ No active session to end")
            return

        if not hasattr(self, "_session_data"):
            logger.warning("⚠️ No session data to save")
            return

        json_path = os.path.join(self._recordings_dir, f"{self._current_session_id}.json")
        
        # Log statistics before saving
        transcript_count = len(self._session_data.get("transcripts", []))
        translation_count = len(self._session_data.get("translations", []))
        logger.info(f"💾 Saving session {self._current_session_id}: {transcript_count} transcripts, {translation_count} translations")
        
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(self._session_data, f, ensure_ascii=False, indent=2)
            logger.info(f"✅ Saved history to {json_path} ({transcript_count} transcripts, {translation_count} translations)")
        except Exception as e:
            logger.error(f"❌ Failed to save history: {e}", exc_info=True)

        # Clear session
        session_id = self._current_session_id
        self._current_session_id = None
        self._session_data = None
        logger.debug(f"🧹 Cleared session: {session_id}")

    def get_all_recordings(self) -> List[Dict]:
        """Get list of all recordings with metadata.

        Returns:
            List of dictionaries with recording info (id, filename, language, date, time, has_transcript).
        """
        recordings = []
        
        # Find all WAV files
        import glob
        wav_files = glob.glob(os.path.join(self._recordings_dir, "meeting_*.wav"))
        
        for wav_path in sorted(wav_files, reverse=True):
            basename = os.path.basename(wav_path)
            recording_id = basename.replace(".wav", "")
            
            # Parse filename: meeting_ja_20251126_211110.wav
            parts = recording_id.split("_")
            
            json_path = os.path.join(self._recordings_dir, f"{recording_id}.json")
            has_transcript = os.path.exists(json_path)
            
            recordings.append({
                "id": recording_id,
                "filename": basename,
                "language": parts[1] if len(parts) > 1 else "unknown",
                "date": parts[2] if len(parts) > 2 else "",
                "time": parts[3] if len(parts) > 3 else "",
                "has_transcript": has_transcript,
                "wav_url": f"/recordings/{basename}",
            })
        
        return recordings

    def get_recording_detail(self, recording_id: str) -> Optional[Dict]:
        """Get detailed transcript and translation data for a recording.

        Args:
            recording_id: The recording ID (e.g., "meeting_ja_20251126_211110").

        Returns:
            Dictionary with transcripts and translations, or None if not found.
        """
        wav_path = os.path.join(self._recordings_dir, f"{recording_id}.wav")
        json_path = os.path.join(self._recordings_dir, f"{recording_id}.json")
        
        if not os.path.exists(wav_path):
            return None
        
        result = {
            "id": recording_id,
            "filename": f"{recording_id}.wav",
            "wav_url": f"/recordings/{recording_id}.wav",
            "transcripts": [],
            "translations": [],
        }
        
        # Try exact match first
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    result["transcripts"] = data.get("transcripts", [])
                    result["translations"] = data.get("translations", [])
                logger.debug(f"✅ Loaded JSON: {json_path}")
            except Exception as e:
                logger.error(f"❌ Failed to load history JSON: {e}")
        else:
            # Try to find JSON with similar timestamp (within 10 seconds)
            # This handles the case where JSON and WAV have slightly different timestamps
            import glob
            import re
            
            # Extract base pattern: meeting_ja_20251126_2225
            pattern_match = re.match(r"(meeting_\w+_\d{8}_\d{4})", recording_id)
            if pattern_match:
                base_pattern = pattern_match.group(1)
                # Find all JSON files with similar pattern
                json_pattern = os.path.join(self._recordings_dir, f"{base_pattern}*.json")
                matching_jsons = glob.glob(json_pattern)
                
                if matching_jsons:
                    # Use the first match (should be closest)
                    closest_json = sorted(matching_jsons)[0]
                    logger.info(f"🔍 Found matching JSON: {os.path.basename(closest_json)} for WAV: {recording_id}.wav")
                    try:
                        with open(closest_json, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            result["transcripts"] = data.get("transcripts", [])
                            result["translations"] = data.get("translations", [])
                        logger.info(f"✅ Loaded JSON from closest match: {closest_json}")
                    except Exception as e:
                        logger.error(f"❌ Failed to load closest JSON: {e}")
                else:
                    logger.warning(f"⚠️ No JSON file found for {recording_id}")
        
        return result


# Global history service instance
history_service = HistoryService()

