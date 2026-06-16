#!/usr/bin/env python3
"""Kiểm tra nhanh màn hình /minutes có chạy thật được không (cần key thật trong .env).

Chạy:  uv run python verify_minutes.py

Gồm 3 bước:
  1. Kiểm tra 2 key có trong .env.
  2. Gemini: sinh biên bản thật từ transcript mẫu tiếng Việt.
  3. Speechmatics: kết nối Real-Time API thật (auth + cấu hình VI + diarization),
     đẩy ~3s audio để xác nhận pipeline nhận và phát frame (không kiểm tra độ chính xác).
"""

import asyncio
import math
import os
import struct
import sys

from dotenv import load_dotenv
from loguru import logger

# Key thật nằm ở root repo (.env của pipecat), không phải .env của example.
# Load example .env trước (nếu có), rồi root .env override để lấy SPEECHMATICS/GEMINI.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT_ENV = os.path.abspath(os.path.join(_HERE, "..", "..", ".env"))
load_dotenv(os.path.join(_HERE, ".env"), override=True)
load_dotenv(_ROOT_ENV, override=True)

OK = "✅"
BAD = "❌"
WARN = "⚠️"


def step1_keys() -> bool:
    print("\n=== B1. Kiểm tra key trong .env ===")
    ok = True
    for k in ["SPEECHMATICS_API_KEY", "GEMINI_API_KEY"]:
        v = os.getenv(k, "")
        if v:
            print(f"{OK} {k}: có ({len(v)} ký tự)")
        else:
            print(f"{BAD} {k}: TRỐNG -> điền vào .env rồi chạy lại")
            ok = False
    return ok


def step2_gemini() -> bool:
    print("\n=== B2. Test Gemini sinh biên bản (thật) ===")
    from minutes_summary import generate_summary_text

    transcripts = [
        {"speaker": "S1", "text": "Chào mọi người, hôm nay ta chốt kế hoạch ra mắt sản phẩm."},
        {"speaker": "S2", "text": "Em đề xuất ra mắt vào cuối tháng 7, cần xong demo trước 20/7."},
        {"speaker": "S1", "text": "Đồng ý. Anh Minh lo phần backend, chị Lan lo marketing."},
        {"speaker": "S2", "text": "Vậy tuần sau họp lại để review tiến độ."},
    ]
    summary = generate_summary_text(transcripts)
    if summary:
        print(f"{OK} Gemini trả về biên bản ({len(summary)} ký tự):\n")
        print("-" * 60)
        print(summary)
        print("-" * 60)
        return True
    print(f"{BAD} Gemini không trả về kết quả (xem log lỗi phía trên).")
    return False


def _make_tone(seconds=3, rate=16000, freq=440):
    """Tạo audio 16-bit mono (tiếng tone) -> bytes, chia khung 20ms."""
    frames = []
    samples_per_chunk = rate // 50  # 20ms
    total = seconds * rate
    buf = bytearray()
    for n in range(total):
        val = int(8000 * math.sin(2 * math.pi * freq * (n / rate)))
        buf += struct.pack("<h", val)
        if len(buf) >= samples_per_chunk * 2:
            frames.append(bytes(buf))
            buf = bytearray()
    if buf:
        frames.append(bytes(buf))
    return frames, rate


async def step3_speechmatics() -> bool:
    print("\n=== B3. Test kết nối Speechmatics Real-Time (thật) ===")
    from pipecat.frames.frames import (
        ErrorFrame,
        InputAudioRawFrame,
        TranscriptionFrame,
    )
    from pipecat.tests.utils import SleepFrame, run_test

    from minutes_bot import build_speechmatics_stt

    stt = build_speechmatics_stt()
    audio_chunks, rate = _make_tone(seconds=3)
    frames_to_send = [
        InputAudioRawFrame(audio=c, sample_rate=rate, num_channels=1)
        for c in audio_chunks
    ]
    frames_to_send.append(SleepFrame(sleep=6))  # chờ kết quả/đóng kết nối

    try:
        down, up = await run_test(stt, frames_to_send=frames_to_send)
    except Exception as e:
        print(f"{BAD} Lỗi khi chạy STT (có thể key sai / không có quyền Real-Time): {e}")
        return False

    errors = [f for f in list(down) + list(up) if isinstance(f, ErrorFrame)]
    transcripts = [f for f in down if isinstance(f, TranscriptionFrame)]

    if errors:
        print(f"{BAD} Speechmatics báo lỗi: {errors[0]}")
        return False

    print(f"{OK} Kết nối + xác thực Speechmatics OK (cấu hình VI + diarization được chấp nhận).")
    if transcripts:
        print(f"   (Nhận {len(transcripts)} transcription frame từ audio tone — chỉ để xác nhận luồng)")
    else:
        print("   (Audio tone không có lời nói nên không có transcript — bình thường. "
              "Độ chính xác tách người nói phải test bằng mic thật trong app.)")
    return True


async def main():
    logger.remove()
    logger.add(sys.stderr, level="WARNING")  # bớt log ồn

    if not step1_keys():
        print(f"\n{WARN} Thiếu key -> dừng. Điền .env rồi chạy lại.")
        return 1

    g = step2_gemini()
    s = await step3_speechmatics()

    print("\n=== KẾT QUẢ ===")
    print(f"Gemini (biên bản):      {OK if g else BAD}")
    print(f"Speechmatics (kết nối): {OK if s else BAD}")
    if g and s:
        print(f"\n{OK} Code chạy được! Giờ chạy `uv run bot.py` và test bằng mic ở /minutes.")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
