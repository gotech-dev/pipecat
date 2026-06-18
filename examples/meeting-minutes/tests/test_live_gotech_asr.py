"""End-to-end THẬT: gọi gateway nội bộ qua GoTechASRSTTService.

OPT-IN: chỉ chạy khi đặt GOTECH_ASR_RUN_LIVE=1 và có GOTECH_ASR_API_KEY.
Mặc định skip để không phụ thuộc mạng/key trong CI.

Chạy:
    GOTECH_ASR_RUN_LIVE=1 GOTECH_ASR_API_KEY=sk-... \
        .venv/bin/python -m pytest tests/test_live_gotech_asr.py -v -s
"""

import os
import shutil
import subprocess
import wave

import pytest

from pipecat.frames.frames import ErrorFrame, TranscriptionFrame

from gotech_asr_stt import GoTechASRSTTService

pytestmark = pytest.mark.skipif(
    not os.getenv("GOTECH_ASR_RUN_LIVE") or not os.getenv("GOTECH_ASR_API_KEY"),
    reason="Live test: cần GOTECH_ASR_RUN_LIVE=1 và GOTECH_ASR_API_KEY",
)


def _make_speech_wav(path: str) -> bool:
    """Tạo WAV giọng nói tiếng Việt bằng macOS `say`. Trả False nếu không có tool."""
    if not (shutil.which("say") and shutil.which("afconvert")):
        return False
    aiff = path + ".aiff"
    subprocess.run(
        ["say", "-v", "Linh", "-o", aiff, "Xin chào, đây là bản kiểm tra nhận dạng giọng nói."],
        check=True,
    )
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", aiff, path], check=True
    )
    return True


@pytest.mark.asyncio
async def test_live_transcribes_real_speech(tmp_path):
    wav_path = str(tmp_path / "speech.wav")
    if not _make_speech_wav(wav_path):
        pytest.skip("Không có `say`/`afconvert` để tạo audio giọng nói")

    with wave.open(wav_path, "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        wav_bytes = open(wav_path, "rb").read()

    svc = GoTechASRSTTService()
    frames = [f async for f in svc.run_stt(wav_bytes)]
    await svc._close_session()

    # Không được có lỗi
    errors = [f for f in frames if isinstance(f, ErrorFrame)]
    assert not errors, f"Gateway trả lỗi: {[e.error for e in errors]}"

    # Phải có ít nhất 1 TranscriptionFrame với text không rỗng
    trans = [f for f in frames if isinstance(f, TranscriptionFrame)]
    assert trans, "Không nhận được TranscriptionFrame"
    text = trans[0].text.lower()
    assert text.strip(), "Text rỗng"
    # nhận dạng tiếng Việt: kỳ vọng có vài từ khoá phổ biến
    assert any(kw in text for kw in ("xin chào", "kiểm", "giọng", "nhận")), f"Text bất ngờ: {text!r}"
    print(f"\n✅ LIVE transcription: {trans[0].text!r}")
