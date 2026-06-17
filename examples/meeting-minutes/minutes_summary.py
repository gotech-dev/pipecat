#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Sinh biên bản cuộc họp (tiếng Việt) bằng LLM từ transcript đã gán người nói.

Pipeline: transcript (có speaker S1/S2...) -> format -> prompt tiếng Việt ->
Google Gemini (Flash - rẻ) -> biên bản (tóm tắt, quyết định, việc cần làm).
Các hàm format/prompt là hàm thuần để dễ unit test; phần gọi LLM tách riêng và
cho phép inject client (mock) khi test.
"""

import os
from typing import Dict, List, Optional

from loguru import logger

# Gemini Flash rẻ & nhanh; đổi qua MINUTES_SUMMARY_MODEL nếu muốn (vd gemini-2.5-flash-lite)
DEFAULT_MODEL = "gemini-2.5-flash"


def speaker_display(label: str, name_map: Optional[Dict[str, str]] = None) -> str:
    """Đổi nhãn 'S1'/'S2' thành 'Người 1'/'Người 2' (hoặc tên thật nếu có name_map)."""
    if name_map and label in name_map and name_map[label].strip():
        return name_map[label].strip()
    if label and label.upper().startswith("S") and label[1:].isdigit():
        return f"Người {label[1:]}"
    return label or "Không rõ"


def format_transcript_for_prompt(
    transcripts: List[Dict], name_map: Optional[Dict[str, str]] = None
) -> str:
    """Ghép transcript thành các dòng 'Người N: nội dung' cho prompt."""
    lines = []
    for t in transcripts:
        text = (t.get("text") or "").strip()
        if not text:
            continue
        who = speaker_display(t.get("speaker", ""), name_map)
        lines.append(f"{who}: {text}")
    return "\n".join(lines)


def build_summary_prompt(transcript_text: str) -> str:
    """Dựng prompt tiếng Việt yêu cầu LLM viết biên bản cuộc họp.

    Prompt ưu tiên ĐẦY ĐỦ hơn ngắn gọn: bản ghi từ STT thường rời rạc, nếu yêu cầu
    "ngắn gọn" thì LLM hay bỏ sót ý phụ -> biên bản thiếu ý. Vì vậy ép liệt kê HẾT
    các chủ đề được nhắc tới, mỗi điểm một gạch đầu dòng.
    """
    return (
        "Bạn là thư ký cuộc họp chuyên nghiệp. Dưới đây là bản ghi lời nói của "
        "cuộc họp, mỗi dòng là một người nói (đã được tách theo giọng nói). Bản ghi "
        "có thể rời rạc, lặp hoặc thiếu dấu câu do nhận dạng giọng nói tự động.\n\n"
        "Hãy viết BIÊN BẢN CUỘC HỌP bằng tiếng Việt, trình bày Markdown gồm các mục:\n"
        "## Tóm tắt nội dung\n"
        "## Các điểm thảo luận chính\n"
        "## Quyết định đã thống nhất\n"
        "## Việc cần làm (ai - làm gì - hạn nếu có)\n\n"
        "Yêu cầu QUAN TRỌNG:\n"
        "- ĐẦY ĐỦ là ưu tiên số 1: liệt kê HẾT mọi chủ đề/ý được nhắc tới trong bản "
        "ghi, KHÔNG bỏ sót, kể cả ý phụ hay nhắc thoáng qua. Thà dài còn hơn thiếu ý.\n"
        "- Mục 'Các điểm thảo luận chính' trình bày dạng gạch đầu dòng, mỗi chủ đề "
        "một dòng; đọc lướt toàn bộ bản ghi (kể cả đoạn đầu và ĐOẠN CUỐI) trước khi viết.\n"
        "- Chỉ dựa trên nội dung bản ghi, KHÔNG bịa thông tin, không suy diễn quá xa.\n"
        "- Nếu một mục không có dữ liệu thì ghi 'Không có'.\n\n"
        "----- BẢN GHI -----\n"
        f"{transcript_text}\n"
        "----- HẾT BẢN GHI -----"
    )


# Cấu hình sinh nội dung: temperature thấp để biên bản ỔN ĐỊNH (bớt "đôi khi thiếu
# ý" do ngẫu nhiên), max_output_tokens đủ lớn để không cụt biên bản họp dài.
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_OUTPUT_TOKENS = 8192


def build_generation_config() -> dict:
    """Trả về dict config cho generate_content (genai coerce dict -> GenerateContentConfig).

    Dùng dict để KHÔNG phải import google.genai ở cấp module (giữ test import nhẹ).
    Chỉnh qua env MINUTES_SUMMARY_TEMPERATURE / MINUTES_SUMMARY_MAX_TOKENS.
    """
    temperature = DEFAULT_TEMPERATURE
    raw_t = os.getenv("MINUTES_SUMMARY_TEMPERATURE", "").strip()
    if raw_t:
        try:
            temperature = float(raw_t)
        except ValueError:
            logger.warning(f"MINUTES_SUMMARY_TEMPERATURE={raw_t!r} không hợp lệ, dùng {temperature}")

    max_tokens = DEFAULT_MAX_OUTPUT_TOKENS
    raw_m = os.getenv("MINUTES_SUMMARY_MAX_TOKENS", "").strip()
    if raw_m:
        try:
            max_tokens = int(raw_m)
        except ValueError:
            logger.warning(f"MINUTES_SUMMARY_MAX_TOKENS={raw_m!r} không hợp lệ, dùng {max_tokens}")

    return {"temperature": temperature, "max_output_tokens": max_tokens}


def _extract_text(response) -> str:
    """Lấy text từ response của Gemini SDK (thuộc tính .text)."""
    text = getattr(response, "text", None)
    if text:
        return text.strip()
    # Fallback: duyệt candidates -> parts (phòng khi .text rỗng)
    parts = []
    for cand in getattr(response, "candidates", []) or []:
        content = getattr(cand, "content", None)
        for part in getattr(content, "parts", []) or []:
            t = getattr(part, "text", None)
            if t:
                parts.append(t)
    return "".join(parts).strip()


def generate_summary_text(
    transcripts: List[Dict],
    *,
    client=None,
    model: Optional[str] = None,
    name_map: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Gọi LLM sinh biên bản từ danh sách transcript. Trả None nếu không thể.

    ``client`` có thể được inject (mock) khi test. Nếu None, tạo Gemini client
    (google.genai) từ GEMINI_API_KEY/GOOGLE_API_KEY.
    """
    transcript_text = format_transcript_for_prompt(transcripts, name_map)
    if not transcript_text.strip():
        logger.warning("⚠️ [minutes] Transcript rỗng, bỏ qua sinh biên bản")
        return None

    if client is None:
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            logger.warning("⚠️ [minutes] Thiếu GEMINI_API_KEY, bỏ qua sinh biên bản")
            return None
        from google import genai

        client = genai.Client(api_key=api_key)

    model = model or os.getenv("MINUTES_SUMMARY_MODEL", DEFAULT_MODEL)
    prompt = build_summary_prompt(transcript_text)
    config = build_generation_config()
    try:
        response = client.models.generate_content(
            model=model, contents=prompt, config=config
        )
    except Exception as e:
        logger.error(f"❌ [minutes] Gọi LLM thất bại: {e}")
        return None

    summary = _extract_text(response)
    return summary or None


def generate_minutes_summary(
    session_id: str,
    *,
    history=None,
    client=None,
    model: Optional[str] = None,
    name_map: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Orchestrator: đọc transcript của session -> sinh biên bản -> ghi vào JSON.

    Trả về summary text, hoặc None nếu không sinh được (không làm crash luồng gọi).
    """
    if history is None:
        from minutes_history_service import minutes_history_service as history

    transcripts = history.load_transcripts(session_id)
    if not transcripts:
        logger.warning(f"⚠️ [minutes] Session {session_id} không có transcript để tóm tắt")
        return None

    summary = generate_summary_text(
        transcripts, client=client, model=model, name_map=name_map
    )
    if summary:
        history.update_summary(session_id, summary)
    return summary
