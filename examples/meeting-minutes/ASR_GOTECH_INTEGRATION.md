# Tích hợp ASR nội bộ (`nemotron-asr`) — phương pháp Master Key

Hướng dẫn tích hợp dịch vụ Speech-to-Text nội bộ của GoTech vào ứng dụng, đi
qua **LiteLLM gateway** (`gateway.gotechjsc.com`) bằng **MASTER KEY**.

> **Khi nào dùng master key?** Master key có toàn quyền trên proxy (gọi mọi
> model, tạo/thu hồi team-key). Dùng cho **service backend tin cậy** chạy trong
> hạ tầng nội bộ — KHÔNG nhúng vào client, mobile app, hay frontend.
> Với nhiều team/người dùng cuối, hãy phát **TEAM_KEY** riêng từ master key.

---

## 0. Trạng thái đã kiểm chứng (2026-06-18)

| Thành phần | Kết quả test | Ghi chú |
|---|---|---|
| **`gateway.gotechjsc.com` + master key + `nemotron-asr`** | ✅ **HTTP 200** → `{"text":"","language":"vi"}` | **đường master key chạy đầy đủ** |
| `gateway.gotechjsc.com/v1/models` | ✅ liệt kê `nemotron-asr` | model đã được mount |
| `asr.gotechjsc.com` (direct, no auth) | HTTP 200, schema `{text,language,elapsed_s}` đúng | đường truyền OK |
| WebSocket `wss://asr.gotechjsc.com/v1/audio/stream` | HTTP 101 Switching Protocols | handshake OK |
| `response_format=text` | trả chuỗi thay vì JSON | đúng spec |

> ✅ **Đã verify qua master key (2026-06-18):** auth + model + transcription
> đều trả `HTTP 200` trên `gateway.gotechjsc.com`. `text` rỗng vì file test là
> sóng sine (không phải giọng nói) — dùng file ghi âm thật để kiểm chất lượng.
>
> ⚠️ **Lưu ý domain:** model `nemotron-asr` **chỉ có trên `gateway.gotechjsc.com`**.
> Domain `code.gotechjsc.com` tuy auth được nhưng **chưa mount** model ASR
> (`/v1/models` không có) → không transcribe được. Luôn dùng `gateway`.

---

## 1. Endpoint & tham số

**Base URL:** `https://gateway.gotechjsc.com/v1`
**Path:** `POST /audio/transcriptions`
**Model:** `nemotron-asr` (bắt buộc khi đi qua LiteLLM)
**Auth:** header `Authorization: Bearer <MASTER_KEY>`

| Field | Bắt buộc | Giá trị |
|---|---|---|
| `file` | có | `wav` / `mp3` / `flac` / `m4a` — ưu tiên 16kHz mono |
| `model` | có (qua LiteLLM) | `nemotron-asr` |
| `language` | nên | `auto`, `vi`, `en`, `en-US`, `ja`, `ko`, `zh-CN`, … (40 locale) |
| `response_format` | không | `json` (mặc định) hoặc `text` |

---

## 2. Cấu hình biến môi trường

Thêm vào `.env` (đã có sẵn trong `.gitignore` — **không commit key**):

```bash
# ASR nội bộ qua LiteLLM (master key — chỉ dùng ở backend tin cậy)
GOTECH_ASR_BASE_URL=https://gateway.gotechjsc.com/v1
GOTECH_ASR_API_KEY=sk-...your-master-key...
GOTECH_ASR_MODEL=nemotron-asr
```

Cập nhật luôn `env.example` (không kèm giá trị thật):

```bash
GOTECH_ASR_BASE_URL=https://gateway.gotechjsc.com/v1
GOTECH_ASR_API_KEY=your_master_key_here
GOTECH_ASR_MODEL=nemotron-asr
```

---

## 3. Gọi nhanh bằng cURL

```bash
curl -X POST https://gateway.gotechjsc.com/v1/audio/transcriptions \
  -H "Authorization: Bearer $GOTECH_ASR_API_KEY" \
  -F "model=nemotron-asr" \
  -F "file=@audio.wav" \
  -F "language=vi"
# → {"text":"xin chào","language":"vi","elapsed_s":0.42}
```

> 💡 Khi test thủ công trong session Claude Code, gõ
> `! curl -H "Authorization: Bearer <KEY>" ...` để key **không lưu** vào lịch sử hội thoại.

---

## 4. Tích hợp OpenAI SDK (Python)

Vì LiteLLM tương thích OpenAI, chỉ cần đổi `base_url` + `api_key` + `model`:

```python
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["GOTECH_ASR_API_KEY"],   # master key
    base_url=os.environ["GOTECH_ASR_BASE_URL"],  # https://gateway.gotechjsc.com/v1
)

with open("audio.wav", "rb") as f:
    out = client.audio.transcriptions.create(
        model=os.environ.get("GOTECH_ASR_MODEL", "nemotron-asr"),
        file=f,
        language="vi",
    )
print(out.text)
```

### Node / TypeScript

```ts
import OpenAI from "openai";
import fs from "fs";

const client = new OpenAI({
  apiKey: process.env.GOTECH_ASR_API_KEY!,       // master key
  baseURL: process.env.GOTECH_ASR_BASE_URL!,     // https://gateway.gotechjsc.com/v1
});

const r = await client.audio.transcriptions.create({
  model: "nemotron-asr",
  file: fs.createReadStream("audio.wav"),
  language: "vi",
});
console.log(r.text);
```

---

## 5. Tích hợp vào pipeline meeting-minutes

Hiện `minutes_bot.py` dùng **Speechmatics** (realtime + diarization). Dịch vụ
`nemotron-asr` qua LiteLLM là **batch transcription** (gửi file, nhận text), nên
phù hợp nhất cho:

- **Hậu kỳ:** transcribe lại file ghi âm trong `recordings/` để đối chiếu / backup.
- **Fallback:** khi Speechmatics hết quota hoặc lỗi key real-time.

> ⚠️ **Lưu ý quan trọng:** API này KHÔNG tách người nói (diarization) như
> Speechmatics. Nếu cần nhãn `S1/S2`, vẫn phải giữ Speechmatics cho luồng chính.
> Endpoint LiteLLM cũng **không hỗ trợ WebSocket** — realtime streaming chỉ có
> ở Endpoint 1 (`asr.gotechjsc.com`).

Ví dụ hàm transcribe file ghi âm (tách riêng, không đụng pipeline realtime):

```python
# gotech_asr.py
import os
from openai import OpenAI

_client = OpenAI(
    api_key=os.environ["GOTECH_ASR_API_KEY"],
    base_url=os.environ["GOTECH_ASR_BASE_URL"],
)

def transcribe_file(path: str, language: str = "vi") -> str:
    """Transcribe một file ghi âm bằng ASR nội bộ (batch)."""
    with open(path, "rb") as f:
        out = _client.audio.transcriptions.create(
            model=os.environ.get("GOTECH_ASR_MODEL", "nemotron-asr"),
            file=f,
            language=language,
        )
    return out.text
```

---

## 6. Smoke test

Tạo file test 16kHz mono rồi gọi thử để xác nhận key + đường truyền:

```bash
# 1. Tạo file WAV test 1 giây
python3 - <<'PY'
import wave, struct, math
with wave.open("/tmp/asr_test.wav", "w") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(b"".join(
        struct.pack("<h", int(3000*math.sin(2*math.pi*440*i/16000)))
        for i in range(16000)))
print("wrote /tmp/asr_test.wav")
PY

# 2. Gọi qua master key
curl -s -w "\n[HTTP %{http_code}]\n" \
  -X POST https://gateway.gotechjsc.com/v1/audio/transcriptions \
  -H "Authorization: Bearer $GOTECH_ASR_API_KEY" \
  -F "model=nemotron-asr" \
  -F "file=@/tmp/asr_test.wav" \
  -F "language=vi"
```

**Kỳ vọng:**
- `HTTP 200` + JSON `{"text": "...", "language": "vi", "elapsed_s": ...}` → key OK.
- File sine ở trên không phải giọng nói nên `text` có thể rỗng (`""`) — vẫn coi
  là **PASS** vì xác nhận được auth + đường đi. Muốn kiểm chất lượng text thì
  dùng file ghi âm giọng nói thật.

| Mã lỗi | Nguyên nhân thường gặp | Cách xử lý |
|---|---|---|
| `401 missing api key` | thiếu header `Authorization` | thêm `-H "Authorization: Bearer <KEY>"` |
| `401 invalid api key` | key sai / đã thu hồi | kiểm tra lại master key trên LiteLLM |
| `400` | thiếu `model` hoặc file lỗi format | thêm `-F "model=nemotron-asr"`, dùng wav 16kHz mono |
| `404` | sai path | đúng phải là `/v1/audio/transcriptions` |

---

## 7. Bảo mật & vận hành

- **Không commit** master key — chỉ để trong `.env` (đã ignore) hoặc secret manager.
- **Không nhúng** master key vào frontend/mobile/client. Phát **TEAM_KEY** riêng
  cho từng nhóm/ứng dụng từ LiteLLM để dễ thu hồi và giới hạn quota.
- Mọi request qua `gateway.gotechjsc.com` được **log + tính billing** chung với
  `digix-coder` — tiện theo dõi usage theo key.
- Khi nghi key lộ: thu hồi trên LiteLLM và xoay (rotate) master key ngay.

---

## 8. Tham chiếu nhanh

| Mục đích | Endpoint | Auth |
|---|---|---|
| Production / multi-team (khuyến nghị) | `gateway.gotechjsc.com/v1` | TEAM_KEY hoặc MASTER_KEY |
| Script test / service nội bộ tin nhau | `asr.gotechjsc.com/v1` | không cần |
| Realtime audio (WebSocket) | `wss://asr.gotechjsc.com/v1/audio/stream` | không cần (chỉ Endpoint 1) |
