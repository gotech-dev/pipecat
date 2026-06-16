# Màn hình `/minutes` — Biên bản cuộc họp tiếng Việt + tách người nói

Màn hình **mới, độc lập** với màn hình `/meeting` cũ (Gladia/dịch). Dùng
**Speechmatics diarization** để transcribe tiếng Việt và **tự tách người nói**
(S1, S2…), kèm **biên bản AI tóm tắt** (Google Gemini Flash - rẻ) cuối cuộc họp.

## Kiến trúc (file mới, không sửa logic cũ)

| File | Vai trò |
|------|---------|
| `minutes_bot.py` | State riêng + `SpeakerTranscriptBroadcaster` + `run_minutes_bot` (pipeline Speechmatics) |
| `minutes_transport.py` | Transport params riêng (chỉ VAD, không smart-turn) |
| `minutes_routes.py` | Routes `/api/minutes/*`, `/ws/minutes-transcripts`, trang `/minutes` |
| `minutes_history_service.py` | Lưu transcript có `speaker` + `summary` (file `minutes_*.json`) |
| `minutes_summary.py` | Sinh biên bản AI từ transcript (LLM) |
| `static/minutes.html` | Giao diện: render theo người nói + biên bản AI |
| `bot.py` | **Chỉ thêm 1 block** gọi `setup_minutes_routes(app, small_webrtc_handler)` |

## Cấu hình `.env`

```env
SPEECHMATICS_API_KEY=...        # bắt buộc — https://portal.speechmatics.com
GEMINI_API_KEY=...              # bắt buộc cho biên bản AI — https://aistudio.google.com/apikey
MINUTES_SUMMARY_MODEL=gemini-2.5-flash    # (tùy chọn) rẻ hơn: gemini-2.5-flash-lite
# Tinh chỉnh diarization (tùy chọn):
MINUTES_OPERATING_POINT=enhanced   # enhanced (chính xác) | standard (nhanh hơn)
MINUTES_MAX_SPEAKERS=              # số người tối đa nếu biết trước, vd 4
MINUTES_SPEAKER_SENSITIVITY=0.5    # 0..1, cao hơn = tách nhạy hơn
```

## Chạy

```bash
uv sync          # cài pipecat[...,speechmatics] + anthropic
uv run bot.py    # mở http://localhost:7860 -> đăng nhập -> /minutes
```

Luồng: `/minutes` → "Bắt đầu ghi âm" → nói nhiều người → nội dung hiện theo
"Người 1 / Người 2…" realtime → "Dừng ghi âm" → biên bản AI tự sinh.

## Test

```bash
uv run --extra ... python -m pytest tests/ -v   # 74 unit test (mock API, không cần key)
```

Unit test bao phủ: broadcaster, cấu hình STT diarization, history (speaker + unicode VN),
sinh biên bản (mock LLM), routes/websocket, frontend wiring, không trùng route với màn cũ.

### Còn cần test thủ công (cần key thật + mic)
- Độ chính xác tách người nói thực tế trên 1 mic chung (tinh chỉnh
  `MINUTES_SPEAKER_SENSITIVITY` / `MINUTES_MAX_SPEAKERS`).
- Chạy song song `/meeting` (Gladia) và `/minutes` (Speechmatics) để xác nhận
  `small_webrtc_handler` dùng chung không xung đột.
