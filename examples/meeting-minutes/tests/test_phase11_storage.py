"""Phase 11 — Unit test lớp lưu trữ storage.py (local + R2, mock boto3, không mạng).

Cover: factory chọn backend theo env, LocalStorage giữ hành vi cũ, R2Storage
upload/exists/url/delete + an toàn prefix + chống path traversal + chọn serve mode.
"""

import pytest

import storage
from storage import LocalStorage, R2Storage, get_storage, reset_storage_singleton


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Xoá mọi env liên quan + reset singleton trước mỗi test."""
    for k in (
        "MINUTES_STORAGE_BACKEND", "MINUTES_R2_SERVE",
        "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
        "R2_ENDPOINT", "R2_BUCKET_NAME", "R2_KEY_PREFIX", "R2_PUBLIC_URL",
    ):
        monkeypatch.delenv(k, raising=False)
    reset_storage_singleton()
    yield
    reset_storage_singleton()


# ------------------------------- fake boto3 client -------------------------
class _FakeS3:
    def __init__(self):
        self.uploaded = []
        self.deleted = []
        self.objects = set()

    def upload_file(self, local_path, bucket, key, ExtraArgs=None):
        self.uploaded.append({"path": local_path, "bucket": bucket, "key": key, "extra": ExtraArgs})
        self.objects.add((bucket, key))

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise Exception("404 Not Found")
        return {}

    def delete_object(self, Bucket, Key):
        self.deleted.append((Bucket, Key))
        self.objects.discard((Bucket, Key))

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"https://signed.example/{Params['Key']}?exp={ExpiresIn}"


def _r2(**kw):
    defaults = dict(
        account_id="acc", access_key_id="ak", secret_access_key="sk",
        endpoint="https://acc.r2.cloudflarestorage.com", bucket="meeting-minutes",
        public_url="https://r2.polypi.ai", client=_FakeS3(),
    )
    defaults.update(kw)
    return R2Storage(**defaults)


# ------------------------------- factory ----------------------------------
def test_factory_defaults_to_local(monkeypatch):
    assert isinstance(get_storage(), LocalStorage)


def test_factory_unknown_backend_falls_back_local(monkeypatch):
    monkeypatch.setenv("MINUTES_STORAGE_BACKEND", "wat")
    assert isinstance(get_storage(), LocalStorage)


def test_factory_r2_when_configured(monkeypatch):
    monkeypatch.setenv("MINUTES_STORAGE_BACKEND", "r2")
    monkeypatch.setenv("R2_BUCKET_NAME", "meeting-minutes")
    assert isinstance(get_storage(), R2Storage)


def test_factory_singleton_cached(monkeypatch):
    assert get_storage() is get_storage()


# ------------------------------- LocalStorage ------------------------------
def test_local_upload_is_noop_and_url(tmp_path):
    s = LocalStorage(recordings_dir=str(tmp_path))
    assert s.upload("/whatever", "minutes_vi_x.wav") is True
    assert s.url("minutes_vi_x.wav") == "/recordings/minutes_vi_x.wav"


def test_local_exists_and_delete(tmp_path):
    f = tmp_path / "minutes_vi_x.wav"
    f.write_bytes(b"RIFF")
    s = LocalStorage(recordings_dir=str(tmp_path))
    assert s.exists("minutes_vi_x.wav") is True
    assert s.delete("minutes_vi_x.wav") is True
    assert s.exists("minutes_vi_x.wav") is False


# ------------------------------- R2Storage ---------------------------------
def test_r2_upload_uses_prefix_and_content_type():
    s = _r2()
    assert s.upload("/tmp/minutes_vi_x.wav", "minutes_vi_x.wav") is True
    call = s._client.uploaded[0]
    assert call["key"] == "recordings/minutes_vi_x.wav"  # prefix mặc định
    assert call["bucket"] == "meeting-minutes"
    assert call["extra"] == {"ContentType": "audio/wav"}


def test_r2_key_strips_path_traversal():
    s = _r2()
    # cố tình truyền path bẩn -> chỉ lấy basename, luôn nằm trong prefix
    assert s._key("../../etc/passwd") == "recordings/passwd"
    assert s._key("/abs/minutes_vi_x.wav") == "recordings/minutes_vi_x.wav"


def test_r2_custom_prefix():
    s = _r2(key_prefix="audio/meetings")  # tự thêm "/" cuối
    assert s._key("a.wav") == "audio/meetings/a.wav"


def test_r2_exists_roundtrip():
    s = _r2()
    assert s.exists("a.wav") is False
    s.upload("/tmp/a.wav", "a.wav")
    assert s.exists("a.wav") is True


def test_r2_delete():
    s = _r2()
    s.upload("/tmp/a.wav", "a.wav")
    assert s.delete("a.wav") is True
    assert ("meeting-minutes", "recordings/a.wav") in s._client.deleted
    assert s.exists("a.wav") is False


def test_r2_url_presigned_by_default():
    s = _r2(serve_mode="presigned")
    url = s.url("a.wav")
    assert url.startswith("https://signed.example/recordings/a.wav")


def test_r2_url_public_when_configured():
    s = _r2(serve_mode="public")
    assert s.url("a.wav") == "https://r2.polypi.ai/recordings/a.wav"


def test_r2_public_falls_back_to_presigned_without_public_url():
    s = _r2(serve_mode="public", public_url="")
    assert s.url("a.wav").startswith("https://signed.example/")
