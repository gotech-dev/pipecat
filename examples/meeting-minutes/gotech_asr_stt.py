#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""STT nội bộ GoTech (nemotron-asr) cho màn hình /minutes — chế độ "AI Nội bộ".

Đi qua LiteLLM gateway (https://gateway.gotechjsc.com/v1) bằng MASTER KEY, dùng
endpoint batch OpenAI-compatible POST /audio/transcriptions.

Thiết kế bám sát phát hiện từ spike (đã verify với giọng nói thật):
- Endpoint chỉ trả text khi nhận trọn 1 đoạn audio (không có partial text live).
- => Kế thừa SegmentedSTTService: base class tự cắt audio theo VAD, đóng gói WAV
  mỗi lượt nói rồi gọi run_stt(wav) đúng 1 lần -> ta POST WAV đó và trả 1 câu final.

Lưu ý: nemotron-asr KHÔNG tách người nói (diarization), nên user_id (speaker) để
trống -> frontend hiển thị "Không rõ". Đây là khác biệt đã được chấp nhận so với
chế độ "AI cao cấp" (Speechmatics) vốn có nhãn S1/S2.

Pattern run_stt copy từ src/pipecat/services/fal/stt.py (FalSTTService).
"""

import os
import re

from typing import AsyncGenerator, Optional

import aiohttp
from loguru import logger

from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601

# Mặc định trỏ tới gateway (LiteLLM) — domain ĐÃ mount nemotron-asr (đã verify).
DEFAULT_BASE_URL = "https://gateway.gotechjsc.com/v1"
DEFAULT_MODEL = "nemotron-asr"

# nemotron-asr BỎ QUA field `language` trong request (đã verify 2026-06-18: ép
# zh-CN/en/ja lên audio tiếng Việt vẫn ra tiếng Việt). Thay vào đó model TỰ
# auto-detect và NHÚNG nhãn ngôn ngữ thật vào cuối text, vd "...nghiệp. <vi-VN>".
# => Không ép được bằng param; phải lọc phía client dựa trên chính nhãn này.
_LANG_TAG_RE = re.compile(r"\s*<([A-Za-z]{2,3}(?:-[A-Za-z0-9]+)?)>\s*$")


def _split_lang_tag(text: str) -> tuple[str, Optional[str]]:
    """Tách nhãn ngôn ngữ <xx-XX> ở cuối text do nemotron-asr nhúng vào.

    Returns:
        (text_đã_sạch, mã_ngôn_ngữ_2_ký_tự_lowercase | None nếu không có nhãn).
    """
    m = _LANG_TAG_RE.search(text)
    if not m:
        return text, None
    code = m.group(1).split("-")[0].lower()
    return text[: m.start()].rstrip(), code


# Khoảng ký tự CJK (Hán + Kana Nhật + Hangul Hàn + dấu câu/fullwidth Á Đông).
# Tiếng Việt chỉ dùng Latin + dấu, KHÔNG bao giờ chứa các ký tự này -> hễ xuất
# hiện là model auto-detect nhầm (vd trả thẳng "生活" mà không kèm nhãn ngôn ngữ).
_CJK_RE = re.compile(
    r"[　-〿぀-ヿㇰ-ㇿ㐀-䶿"
    r"一-鿿가-힯豈-﫿＀-￯]"
)


def _has_cjk(text: str) -> bool:
    """True nếu text chứa bất kỳ ký tự CJK nào (chắc chắn không phải tiếng Việt)."""
    return bool(_CJK_RE.search(text))


class GoTechASRSTTService(SegmentedSTTService):
    """STT batch nội bộ: mỗi lượt nói (VAD segment) -> 1 request HTTP -> 1 câu final."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        language: Language = Language.VI,
        sample_rate: Optional[int] = None,
        **kwargs,
    ):
        """Khởi tạo service.

        Args:
            api_key: MASTER/TEAM key của LiteLLM. Mặc định lấy GOTECH_ASR_API_KEY.
            base_url: Base URL gateway. Mặc định GOTECH_ASR_BASE_URL hoặc gateway.
            model: Tên model ASR. Mặc định GOTECH_ASR_MODEL hoặc "nemotron-asr".
            language: Ngôn ngữ nhận dạng (mặc định tiếng Việt).
            sample_rate: Để None -> lấy theo pipeline; server tự xử lý sample rate.
            **kwargs: chuyển tiếp cho SegmentedSTTService.
        """
        super().__init__(sample_rate=sample_rate, **kwargs)
        self._api_key = api_key or os.getenv("GOTECH_ASR_API_KEY", "")
        self._base_url = (
            base_url or os.getenv("GOTECH_ASR_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self._model = model or os.getenv("GOTECH_ASR_MODEL") or DEFAULT_MODEL
        self._language = language
        self._session: Optional[aiohttp.ClientSession] = None

        if not self._api_key:
            logger.warning(
                "[gotech-asr] GOTECH_ASR_API_KEY trống -> request sẽ bị 401. "
                "Đặt key vào .env trước khi dùng chế độ AI Nội bộ."
            )

    @property
    def _endpoint(self) -> str:
        return f"{self._base_url}/audio/transcriptions"

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def stop(self, frame):
        """Đóng HTTP session khi pipeline dừng."""
        await super().stop(frame)
        await self._close_session()

    async def cancel(self, frame):
        """Đóng HTTP session khi pipeline bị huỷ."""
        await super().cancel(frame)
        await self._close_session()

    async def _close_session(self):
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        """Transcribe 1 đoạn audio (WAV sẵn từ base class) qua gateway nội bộ.

        Args:
            audio: WAV bytes (16-bit mono) do SegmentedSTTService đóng gói.

        Yields:
            TranscriptionFrame nếu có text, hoặc ErrorFrame khi lỗi.
        """
        try:
            await self.start_processing_metrics()
            await self.start_ttfb_metrics()

            session = await self._ensure_session()
            form = aiohttp.FormData()
            form.add_field("model", self._model)
            form.add_field("language", self._language.value)
            form.add_field(
                "file",
                audio,
                filename="audio.wav",
                content_type="audio/wav",
            )
            headers = {"Authorization": f"Bearer {self._api_key}"}

            async with session.post(
                self._endpoint, data=form, headers=headers
            ) as resp:
                await self.stop_ttfb_metrics()
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"[gotech-asr] HTTP {resp.status}: {body[:200]}")
                    yield ErrorFrame(error=f"GoTech ASR HTTP {resp.status}: {body[:120]}")
                    return
                data = await resp.json()

            raw = (data.get("text") or "").strip()
            text, detected = _split_lang_tag(raw)

            # Model auto-detect và bỏ qua field language -> nếu nó tự nhận segment
            # KHÔNG phải tiếng Việt (vd <zh-CN>), bỏ hẳn segment để tránh ký tự
            # tiếng Trung lọt vào biên bản tiếng Việt.
            want = self._language.value.split("-")[0].lower()
            if detected and detected != want:
                logger.info(
                    f"[gotech-asr] Bỏ segment non-{want} (model detect '{detected}'): [{text}]"
                )
                return

            # Lưới chặn cuối: model có thể trả ký tự Hán/Kana/Hangul mà KHÔNG kèm
            # nhãn ngôn ngữ -> nhãn ở trên không bắt được. Tiếng Việt không bao giờ
            # có ký tự CJK nên cứ thấy là bỏ.
            if _has_cjk(text):
                logger.info(f"[gotech-asr] Bỏ segment chứa ký tự CJK (detect nhầm): [{text}]")
                return

            if text:  # chỉ phát khi có nội dung (tránh lưu câu rỗng vào lịch sử)
                logger.debug(f"[gotech-asr] Transcription: [{text}]")
                yield TranscriptionFrame(
                    text,
                    self._user_id,  # rỗng với WebRTC -> speaker "Không rõ"
                    time_now_iso8601(),
                    self._language,
                    result=data,
                )
        except Exception as e:  # pragma: no cover - lỗi mạng runtime
            logger.error(f"[gotech-asr] exception: {e}")
            yield ErrorFrame(error=f"GoTech ASR error: {e}")
        finally:
            await self.stop_processing_metrics()
