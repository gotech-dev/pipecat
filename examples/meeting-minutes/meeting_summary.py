#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Sinh biên bản cuộc họp SONG NGỮ cho màn hình /meeting (Gladia).

Khác với minutes_summary (chỉ tiếng Việt, có tách người nói): màn /meeting ghi
âm bằng ngôn ngữ gốc (Nhật/Anh) và đã dịch realtime sang tiếng Việt. Ở đây ta
sinh biên bản gồm HAI phần: ngôn ngữ gốc + tiếng Việt, để người dùng đọc đối
chiếu.

Tái dùng hạ tầng gọi Gemini từ ``minutes_summary`` (build_generation_config,
_extract_text, DEFAULT_MODEL) để không lặp code. Các hàm format/prompt là hàm
thuần (pure) để dễ unit test; phần gọi LLM tách riêng và cho phép inject client.
"""

import os
from typing import Dict, List, Optional

from loguru import logger

from minutes_summary import (
    DEFAULT_MODEL,
    _extract_text,
    build_generation_config,
)

# Tên ngôn ngữ gốc hiển thị trong biên bản (theo mã trong tên file meeting_{lang}_...)
SOURCE_LANGUAGE_NAMES = {
    "ja": "tiếng Nhật",
    "en": "tiếng Anh",
}


def source_language_from_session(session_id: str, fallback: str = "ja") -> str:
    """Suy ra mã ngôn ngữ gốc từ session_id 'meeting_{lang}_YYYYMMDD_HHMMSS'."""
    parts = session_id.split("_")  # ['meeting', 'ja', 'YYYYMMDD', 'HHMMSS']
    if len(parts) > 1 and parts[1]:
        return parts[1]
    return fallback


def format_meeting_transcript(transcripts: List[Dict]) -> str:
    """Ghép các câu transcription (ngôn ngữ gốc) thành 1 đoạn văn cho prompt.

    Màn /meeting không tách người nói nên chỉ nối liền text các câu final.
    """
    lines = []
    for t in transcripts:
        text = (t.get("text") or "").strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def build_bilingual_summary_prompt(transcript_text: str, source_lang: str) -> str:
    """Dựng prompt yêu cầu LLM viết biên bản SONG NGỮ (ngôn ngữ gốc + tiếng Việt).

    Ưu tiên ĐẦY ĐỦ hơn ngắn gọn (bản ghi STT rời rạc, dễ sót ý). Ép liệt kê hết
    chủ đề, mỗi điểm một gạch đầu dòng, ở CẢ hai ngôn ngữ.
    """
    src_name = SOURCE_LANGUAGE_NAMES.get(source_lang, "ngôn ngữ gốc")
    return (
        "Bạn là thư ký cuộc họp chuyên nghiệp. Dưới đây là bản ghi lời nói của một "
        f"cuộc họp bằng {src_name}. Bản ghi do nhận dạng giọng nói tự động nên có "
        "thể rời rạc, lặp hoặc thiếu dấu câu.\n\n"
        "Hãy viết BIÊN BẢN CUỘC HỌP trình bày Markdown, gồm HAI phần song ngữ theo "
        "đúng thứ tự sau:\n\n"
        f"# Biên bản ({src_name})\n"
        f"Viết toàn bộ biên bản bằng {src_name}, với các mục con:\n"
        "## Tóm tắt nội dung\n"
        "## Các điểm thảo luận chính\n"
        "## Quyết định đã thống nhất\n"
        "## Việc cần làm\n\n"
        "# Biên bản (Tiếng Việt)\n"
        "Dịch/viết lại biên bản trên bằng tiếng Việt, cùng cấu trúc mục con.\n\n"
        "Yêu cầu QUAN TRỌNG:\n"
        "- ĐẦY ĐỦ là ưu tiên số 1: liệt kê HẾT mọi chủ đề/ý được nhắc tới, KHÔNG bỏ "
        "sót, kể cả ý phụ. Thà dài còn hơn thiếu ý.\n"
        "- Hai phần phải khớp nội dung với nhau (cùng ý, chỉ khác ngôn ngữ).\n"
        "- 'Các điểm thảo luận chính' trình bày dạng gạch đầu dòng; đọc lướt toàn bộ "
        "bản ghi (kể cả đoạn đầu và ĐOẠN CUỐI) trước khi viết.\n"
        "- Chỉ dựa trên nội dung bản ghi, KHÔNG bịa thông tin.\n"
        "- Nếu một mục không có dữ liệu thì ghi 'Không có' / nội dung tương đương.\n\n"
        "----- BẢN GHI -----\n"
        f"{transcript_text}\n"
        "----- HẾT BẢN GHI -----"
    )


def generate_bilingual_summary_text(
    transcripts: List[Dict],
    source_lang: str,
    *,
    client=None,
    model: Optional[str] = None,
) -> Optional[str]:
    """Gọi LLM sinh biên bản song ngữ. Trả None nếu không thể.

    ``client`` có thể inject (mock) khi test. Nếu None, tạo Gemini client từ
    GEMINI_API_KEY/GOOGLE_API_KEY.
    """
    transcript_text = format_meeting_transcript(transcripts)
    if not transcript_text.strip():
        logger.warning("⚠️ [meeting] Transcript rỗng, bỏ qua sinh biên bản")
        return None

    if client is None:
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            logger.warning("⚠️ [meeting] Thiếu GEMINI_API_KEY, bỏ qua sinh biên bản")
            return None
        from google import genai

        client = genai.Client(api_key=api_key)

    model = model or os.getenv("MINUTES_SUMMARY_MODEL", DEFAULT_MODEL)
    prompt = build_bilingual_summary_prompt(transcript_text, source_lang)
    config = build_generation_config()
    try:
        response = client.models.generate_content(
            model=model, contents=prompt, config=config
        )
    except Exception as e:
        logger.error(f"❌ [meeting] Gọi LLM thất bại: {e}")
        return None

    summary = _extract_text(response)
    return summary or None


def generate_meeting_summary(
    session_id: str,
    *,
    history=None,
    client=None,
    model: Optional[str] = None,
) -> Optional[str]:
    """Orchestrator: đọc transcript của session -> sinh biên bản song ngữ -> ghi JSON.

    Trả về summary text, hoặc None nếu không sinh được (không làm crash luồng gọi).
    """
    if history is None:
        from history_service import history_service as history

    transcripts = history.load_transcripts(session_id)
    if not transcripts:
        logger.warning(f"⚠️ [meeting] Session {session_id} không có transcript để tóm tắt")
        return None

    source_lang = source_language_from_session(session_id)
    summary = generate_bilingual_summary_text(
        transcripts, source_lang, client=client, model=model
    )
    if summary:
        history.update_summary(session_id, summary)
    return summary
