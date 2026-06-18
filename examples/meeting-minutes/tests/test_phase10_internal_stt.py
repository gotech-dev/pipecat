"""Phase 10 — Unit test chi tiết GoTechASRSTTService (mock aiohttp, không mạng).

Cover toàn bộ nhánh run_stt: thành công / text rỗng / lỗi HTTP / exception, và
vòng đời session (ensure/close/stop/cancel) + cảnh báo thiếu key.
"""

import pytest

from pipecat.frames.frames import CancelFrame, EndFrame, ErrorFrame, StartFrame, TranscriptionFrame
from pipecat.transcriptions.language import Language

from gotech_asr_stt import GoTechASRSTTService, _has_cjk, _split_lang_tag


# --------------------------- fakes aiohttp ------------------------------
class _FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def text(self):
        return str(self._payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, resp=None, raise_exc=None):
        self._resp = resp
        self._raise = raise_exc
        self.closed = False
        self.posted = []

    def post(self, url, data=None, headers=None):
        self.posted.append({"url": url, "data": data, "headers": headers})
        if self._raise:
            raise self._raise
        return self._resp

    async def close(self):
        self.closed = True


async def _collect(agen):
    return [f async for f in agen]


def _svc(monkeypatch, **env):
    for k in ("GOTECH_ASR_API_KEY", "GOTECH_ASR_BASE_URL", "GOTECH_ASR_MODEL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return GoTechASRSTTService()


# --------------------------- run_stt success ----------------------------
@pytest.mark.asyncio
async def test_run_stt_success_yields_transcription(monkeypatch):
    svc = _svc(monkeypatch, GOTECH_ASR_API_KEY="k")
    svc._session = _FakeSession(resp=_FakeResp(200, {"text": "Xin chào", "language": "vi"}))

    frames = await _collect(svc.run_stt(b"WAVDATA"))
    assert len(frames) == 1
    f = frames[0]
    assert isinstance(f, TranscriptionFrame)
    assert f.text == "Xin chào"
    assert f.user_id == ""  # không diarization
    assert f.language == Language.VI


@pytest.mark.asyncio
async def test_run_stt_sends_correct_form_and_auth(monkeypatch):
    svc = _svc(monkeypatch, GOTECH_ASR_API_KEY="secret", GOTECH_ASR_MODEL="nemotron-asr")
    sess = _FakeSession(resp=_FakeResp(200, {"text": "hi"}))
    svc._session = sess

    await _collect(svc.run_stt(b"WAVDATA"))
    call = sess.posted[0]
    assert call["url"].endswith("/audio/transcriptions")
    assert call["headers"]["Authorization"] == "Bearer secret"


# --------------------------- nhãn ngôn ngữ auto-detect ------------------
# nemotron-asr bỏ qua field `language`, tự auto-detect và nhúng nhãn <xx-XX>
# vào cuối text. Client phải cắt nhãn + bỏ segment không phải tiếng Việt.
def test_split_lang_tag_variants():
    assert _split_lang_tag("Chào. <vi-VN>") == ("Chào.", "vi")
    assert _split_lang_tag("你好 <zh-CN>") == ("你好", "zh")
    assert _split_lang_tag("hi <en>") == ("hi", "en")
    assert _split_lang_tag("không có nhãn") == ("không có nhãn", None)


@pytest.mark.asyncio
async def test_run_stt_strips_vi_tag(monkeypatch):
    svc = _svc(monkeypatch, GOTECH_ASR_API_KEY="k")
    svc._session = _FakeSession(
        resp=_FakeResp(200, {"text": "Đấy là doanh nghiệp. <vi-VN>", "language": "vi"})
    )
    frames = await _collect(svc.run_stt(b"x"))
    assert len(frames) == 1
    assert frames[0].text == "Đấy là doanh nghiệp."  # nhãn đã bị cắt


@pytest.mark.asyncio
async def test_run_stt_drops_non_vietnamese_segment(monkeypatch):
    svc = _svc(monkeypatch, GOTECH_ASR_API_KEY="k")
    # model tự detect tiếng Trung (dù ta gửi language=vi) -> bỏ hẳn, không nhả
    svc._session = _FakeSession(resp=_FakeResp(200, {"text": "请不吝点赞 <zh-CN>", "language": "vi"}))
    assert await _collect(svc.run_stt(b"x")) == []


def test_has_cjk():
    assert _has_cjk("生活") is True
    assert _has_cjk("Xin chào 你好") is True  # lẫn 1 ký tự Hán cũng bắt
    assert _has_cjk("こんにちは") is True  # kana Nhật
    assert _has_cjk("안녕하세요") is True  # hangul Hàn
    assert _has_cjk("Rất là cam kết cho") is False
    assert _has_cjk("Báo cáo KPI quý 3") is False


@pytest.mark.asyncio
async def test_run_stt_drops_cjk_without_tag(monkeypatch):
    svc = _svc(monkeypatch, GOTECH_ASR_API_KEY="k")
    # model trả thẳng chữ Hán KHÔNG kèm nhãn ngôn ngữ -> nhãn không bắt được,
    # lưới CJK phải chặn.
    svc._session = _FakeSession(resp=_FakeResp(200, {"text": "生活", "language": "vi"}))
    assert await _collect(svc.run_stt(b"x")) == []


@pytest.mark.asyncio
async def test_run_stt_keeps_text_without_tag(monkeypatch):
    svc = _svc(monkeypatch, GOTECH_ASR_API_KEY="k")
    # không có nhãn -> không suy ra non-vi, vẫn giữ nguyên text
    svc._session = _FakeSession(resp=_FakeResp(200, {"text": "Một câu tiếng Việt", "language": "vi"}))
    frames = await _collect(svc.run_stt(b"x"))
    assert len(frames) == 1 and frames[0].text == "Một câu tiếng Việt"


# --------------------------- run_stt edge cases -------------------------
@pytest.mark.asyncio
async def test_run_stt_empty_text_yields_nothing(monkeypatch):
    svc = _svc(monkeypatch, GOTECH_ASR_API_KEY="k")
    svc._session = _FakeSession(resp=_FakeResp(200, {"text": "   "}))
    assert await _collect(svc.run_stt(b"x")) == []


@pytest.mark.asyncio
async def test_run_stt_missing_text_key_yields_nothing(monkeypatch):
    svc = _svc(monkeypatch, GOTECH_ASR_API_KEY="k")
    svc._session = _FakeSession(resp=_FakeResp(200, {"language": "vi"}))
    assert await _collect(svc.run_stt(b"x")) == []


@pytest.mark.asyncio
async def test_run_stt_http_error_yields_errorframe(monkeypatch):
    svc = _svc(monkeypatch, GOTECH_ASR_API_KEY="k")
    svc._session = _FakeSession(resp=_FakeResp(401, {"error": "missing api key"}))
    frames = await _collect(svc.run_stt(b"x"))
    assert len(frames) == 1 and isinstance(frames[0], ErrorFrame)
    assert "401" in frames[0].error


@pytest.mark.asyncio
async def test_run_stt_exception_yields_errorframe(monkeypatch):
    svc = _svc(monkeypatch, GOTECH_ASR_API_KEY="k")
    svc._session = _FakeSession(raise_exc=RuntimeError("boom"))
    frames = await _collect(svc.run_stt(b"x"))
    assert len(frames) == 1 and isinstance(frames[0], ErrorFrame)
    assert "boom" in frames[0].error


# --------------------------- session lifecycle --------------------------
@pytest.mark.asyncio
async def test_ensure_session_creates_and_reuses(monkeypatch):
    svc = _svc(monkeypatch, GOTECH_ASR_API_KEY="k")
    s1 = await svc._ensure_session()
    s2 = await svc._ensure_session()
    assert s1 is s2  # tái dùng cùng 1 session
    await svc._close_session()
    assert svc._session is None


@pytest.mark.asyncio
async def test_close_session_idempotent(monkeypatch):
    svc = _svc(monkeypatch, GOTECH_ASR_API_KEY="k")
    await svc._close_session()  # chưa có session -> không lỗi
    svc._session = _FakeSession()
    await svc._close_session()
    assert svc._session is None


@pytest.mark.asyncio
async def test_stop_closes_session(monkeypatch):
    svc = _svc(monkeypatch, GOTECH_ASR_API_KEY="k")
    sess = _FakeSession()
    svc._session = sess
    await svc.stop(EndFrame())
    assert sess.closed is True
    assert svc._session is None


@pytest.mark.asyncio
async def test_cancel_closes_session(monkeypatch):
    svc = _svc(monkeypatch, GOTECH_ASR_API_KEY="k")
    sess = _FakeSession()
    svc._session = sess
    await svc.cancel(CancelFrame())
    assert sess.closed is True


# --------------------------- config / warning ---------------------------
def test_no_api_key_does_not_raise(monkeypatch):
    svc = _svc(monkeypatch)  # không set key -> chỉ cảnh báo, không raise
    assert svc._api_key == ""


def test_base_url_trailing_slash_normalized(monkeypatch):
    svc = _svc(monkeypatch, GOTECH_ASR_API_KEY="k", GOTECH_ASR_BASE_URL="https://x/v1/")
    assert svc._endpoint == "https://x/v1/audio/transcriptions"
