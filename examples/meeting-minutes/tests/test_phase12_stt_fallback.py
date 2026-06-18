"""Phase 12 — Test đường chọn STT premium: Speechmatics + fallback Gladia.

Bổ sung cover cho các hàm mà build_minutes_stt("premium") đi qua sau khi gộp:
_check_speechmatics_key, speechmatics_available, build_gladia_fallback_stt.
Mock urllib/env -> không gọi mạng.
"""

import urllib.error
import urllib.request

import pytest

import minutes_bot as mb


# --------------------------- _check_speechmatics_key --------------------
class _FakeOK:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_check_key_valid(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _FakeOK())
    assert mb._check_speechmatics_key("k") is True


def test_check_key_rejected_401(monkeypatch):
    def _raise(req, timeout=None):
        raise urllib.error.HTTPError("u", 401, "unauth", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    assert mb._check_speechmatics_key("k") is False


def test_check_key_server_error_fail_open(monkeypatch):
    def _raise(req, timeout=None):
        raise urllib.error.HTTPError("u", 500, "err", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    assert mb._check_speechmatics_key("k") is True  # fail-open


def test_check_key_network_error_fail_open(monkeypatch):
    def _raise(req, timeout=None):
        raise OSError("network down")

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    assert mb._check_speechmatics_key("k") is True  # fail-open


# --------------------------- speechmatics_available ---------------------
@pytest.mark.asyncio
async def test_speechmatics_unavailable_when_no_key(monkeypatch):
    monkeypatch.delenv("SPEECHMATICS_API_KEY", raising=False)
    assert await mb.speechmatics_available() is False


@pytest.mark.asyncio
async def test_speechmatics_available_when_key_valid(monkeypatch):
    monkeypatch.setenv("SPEECHMATICS_API_KEY", "k")
    monkeypatch.setattr(mb, "_check_speechmatics_key", lambda key: True)
    assert await mb.speechmatics_available() is True


@pytest.mark.asyncio
async def test_speechmatics_unavailable_when_key_rejected(monkeypatch):
    monkeypatch.setenv("SPEECHMATICS_API_KEY", "k")
    monkeypatch.setattr(mb, "_check_speechmatics_key", lambda key: False)
    assert await mb.speechmatics_available() is False


# --------------------------- build_gladia_fallback_stt ------------------
def test_build_gladia_fallback_returns_service(monkeypatch):
    monkeypatch.setenv("GLADIA_API_KEY", "dummy")
    from pipecat.services.gladia.stt import GladiaSTTService

    assert isinstance(mb.build_gladia_fallback_stt(), GladiaSTTService)


# --------------------------- build_minutes_stt: premium real path -------
@pytest.mark.asyncio
async def test_premium_uses_speechmatics_when_available(monkeypatch):
    """Premium + key hợp lệ -> Speechmatics (KHÔNG fallback)."""
    monkeypatch.setenv("SPEECHMATICS_API_KEY", "k")
    monkeypatch.setattr(mb, "_check_speechmatics_key", lambda key: True)
    from pipecat.services.speechmatics.stt import SpeechmaticsSTTService

    stt = await mb.build_minutes_stt("premium")
    assert isinstance(stt, SpeechmaticsSTTService)


@pytest.mark.asyncio
async def test_premium_falls_back_to_gladia_when_unavailable(monkeypatch):
    """Premium + Speechmatics lỗi -> Gladia."""
    monkeypatch.setenv("GLADIA_API_KEY", "dummy")
    monkeypatch.delenv("SPEECHMATICS_API_KEY", raising=False)  # không key -> unavailable
    from pipecat.services.gladia.stt import GladiaSTTService

    stt = await mb.build_minutes_stt("premium")
    assert isinstance(stt, GladiaSTTService)
