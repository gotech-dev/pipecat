#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""History service cho màn hình /minutes.

Khác với history_service cũ:
- Mỗi transcript có field ``speaker`` (S1/S2... từ diarization).
- Lưu thêm ``summary`` (biên bản AI tóm tắt) vào file JSON.
- Namespace file ``minutes_*`` để KHÔNG đụng tới các file ``meeting_*`` của màn hình cũ.
"""

import glob
import json
import os
from typing import Dict, List, Optional

from loguru import logger

FILE_PREFIX = "minutes_"


class MinutesHistoryService:
    """Lưu & truy xuất transcript (có nhãn người nói) + biên bản tóm tắt."""

    def __init__(self, recordings_dir: str = "recordings"):
        self._recordings_dir = recordings_dir
        os.makedirs(recordings_dir, exist_ok=True)
        self._current_session_id: Optional[str] = None
        self._session_data: Optional[dict] = None

    # ---------------------------------------------------------------- session
    def start_session(self, session_id: str):
        self._current_session_id = session_id
        self._session_data = {
            "session_id": session_id,
            "language": "vi",
            "transcripts": [],
            "summary": None,
        }
        logger.info(f"📚 [minutes] Bắt đầu session: {session_id}")

    def save_transcript(self, message: dict):
        """Lưu 1 câu (đã final) kèm speaker vào session hiện tại."""
        if not self._current_session_id or self._session_data is None:
            logger.warning("⚠️ [minutes] Chưa có session active để lưu transcript")
            return
        # Chỉ giữ các field cần thiết cho biên bản
        self._session_data["transcripts"].append(
            {
                "speaker": message.get("speaker", ""),
                "text": message.get("text", ""),
                "timestamp": message.get("timestamp"),
                "is_final": message.get("is_final", True),
            }
        )

    def set_summary(self, summary: str):
        """Gán summary vào session đang mở (nếu còn mở)."""
        if self._session_data is not None:
            self._session_data["summary"] = summary

    def end_session(self) -> Optional[str]:
        """Đóng session, ghi ra JSON. Trả về đường dẫn file (hoặc None)."""
        if not self._current_session_id or self._session_data is None:
            logger.warning("⚠️ [minutes] Không có session để đóng")
            return None

        json_path = os.path.join(
            self._recordings_dir, f"{self._current_session_id}.json"
        )
        count = len(self._session_data.get("transcripts", []))
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(self._session_data, f, ensure_ascii=False, indent=2)
            logger.info(f"✅ [minutes] Lưu {count} câu -> {json_path}")
        except Exception as e:  # pragma: no cover
            logger.error(f"❌ [minutes] Lưu history thất bại: {e}", exc_info=True)
            json_path = None

        self._current_session_id = None
        self._session_data = None
        return json_path

    # ---------------------------------------------------------------- summary
    def load_transcripts(self, session_id: str) -> List[Dict]:
        """Đọc danh sách câu (final) của 1 session đã lưu."""
        data = self._load_json(session_id)
        if not data:
            return []
        return [t for t in data.get("transcripts", []) if t.get("is_final", True)]

    def update_summary(self, session_id: str, summary: str) -> bool:
        """Ghi summary vào file JSON của session đã đóng. Trả True nếu thành công."""
        data = self._load_json(session_id)
        if data is None:
            logger.warning(f"⚠️ [minutes] Không tìm thấy session {session_id} để ghi summary")
            return False
        data["summary"] = summary
        json_path = os.path.join(self._recordings_dir, f"{session_id}.json")
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"✅ [minutes] Đã ghi biên bản AI vào {json_path}")
            return True
        except Exception as e:  # pragma: no cover
            logger.error(f"❌ [minutes] Ghi summary thất bại: {e}")
            return False

    def get_summary(self, session_id: str) -> Optional[str]:
        data = self._load_json(session_id)
        return data.get("summary") if data else None

    # ---------------------------------------------------------------- listing
    def get_all_recordings(self) -> List[Dict]:
        recordings = []
        wav_files = glob.glob(os.path.join(self._recordings_dir, f"{FILE_PREFIX}*.wav"))
        for wav_path in sorted(wav_files, reverse=True):
            basename = os.path.basename(wav_path)
            recording_id = basename.replace(".wav", "")
            parts = recording_id.split("_")  # minutes_vi_YYYYMMDD_HHMMSS
            json_path = os.path.join(self._recordings_dir, f"{recording_id}.json")
            recordings.append(
                {
                    "id": recording_id,
                    "filename": basename,
                    "language": parts[1] if len(parts) > 1 else "vi",
                    "date": parts[2] if len(parts) > 2 else "",
                    "time": parts[3] if len(parts) > 3 else "",
                    "has_transcript": os.path.exists(json_path),
                    "wav_url": f"/recordings/{basename}",
                }
            )
        return recordings

    def get_recording_detail(self, recording_id: str) -> Optional[Dict]:
        data = self._load_json(recording_id)
        if data is None:
            return None
        return {
            "id": recording_id,
            "filename": f"{recording_id}.wav",
            "wav_url": f"/recordings/{recording_id}.wav",
            "transcripts": data.get("transcripts", []),
            "summary": data.get("summary"),
        }

    # ---------------------------------------------------------------- helpers
    def _load_json(self, session_id: str) -> Optional[dict]:
        json_path = os.path.join(self._recordings_dir, f"{session_id}.json")
        if not os.path.exists(json_path):
            return None
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:  # pragma: no cover
            logger.error(f"❌ [minutes] Đọc JSON thất bại: {e}")
            return None


# Singleton global dùng bởi broadcaster + routes
minutes_history_service = MinutesHistoryService()
