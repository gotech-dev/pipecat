#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Lưu trữ file ghi âm WAV của màn /minutes: **local** (mặc định) hoặc **Cloudflare R2**.

Chọn backend qua env ``MINUTES_STORAGE_BACKEND`` = ``local`` | ``r2`` (mặc định
``local`` -> KHÔNG đổi hành vi hiện tại, không cần mạng/credential).

Thiết kế an toàn cho bucket:
- R2 dùng S3-compatible API (boto3). MỌI key đều nằm dưới prefix ``R2_KEY_PREFIX``
  (mặc định ``recordings/``) -> không bao giờ đụng dữ liệu ngoài prefix.
- Không list toàn bucket trong luồng app (listing dựa trên JSON local).
- Phục vụ: ``presigned`` (riêng tư, hết hạn) hoặc ``public`` (qua R2_PUBLIC_URL).
  Chọn qua ``MINUTES_R2_SERVE`` (mặc định ``presigned``).

Interface chung (theo *filename* dạng "minutes_vi_YYYYMMDD_HHMMSS.wav"):
- ``upload(local_path, filename) -> bool``
- ``exists(filename) -> bool``
- ``url(filename) -> str``        # URL phục vụ cho frontend
- ``delete(filename) -> bool``
"""

import os
from typing import Optional

from loguru import logger

DEFAULT_KEY_PREFIX = "recordings/"
DEFAULT_PRESIGN_EXPIRES = 3600  # giây (1h)


def _env(name: str, default: str = "") -> str:
    """os.getenv + strip (phòng .env có khoảng trắng/CR thừa)."""
    return (os.getenv(name) or default).strip()


class LocalStorage:
    """Backend local: file đã nằm sẵn trong ``recordings/``, phục vụ qua mount tĩnh.

    ``upload`` là no-op (file đã ở local). Giữ nguyên hành vi cũ hoàn toàn.
    """

    def __init__(self, recordings_dir: str = "recordings", base_url: str = "/recordings"):
        self._dir = recordings_dir
        self._base_url = base_url.rstrip("/")

    def upload(self, local_path: str, filename: str) -> bool:  # noqa: ARG002
        return True  # không cần làm gì, file đã ở local

    def exists(self, filename: str) -> bool:
        return os.path.exists(os.path.join(self._dir, filename))

    def url(self, filename: str) -> str:
        return f"{self._base_url}/{filename}"

    def delete(self, filename: str) -> bool:
        path = os.path.join(self._dir, filename)
        try:
            if os.path.exists(path):
                os.remove(path)
            return True
        except OSError as e:  # pragma: no cover
            logger.error(f"[storage:local] Xoá {path} thất bại: {e}")
            return False


class R2Storage:
    """Backend Cloudflare R2 (S3-compatible) — chỉ thao tác trong ``key_prefix``."""

    def __init__(
        self,
        *,
        account_id: Optional[str] = None,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        bucket: Optional[str] = None,
        public_url: Optional[str] = None,
        key_prefix: Optional[str] = None,
        serve_mode: Optional[str] = None,
        client=None,
    ):
        self._bucket = (bucket or _env("R2_BUCKET_NAME")).strip()
        self._endpoint = (endpoint or _env("R2_ENDPOINT")).strip()
        self._access_key = (access_key_id or _env("R2_ACCESS_KEY_ID")).strip()
        self._secret = (secret_access_key or _env("R2_SECRET_ACCESS_KEY")).strip()
        self._public_url = (public_url or _env("R2_PUBLIC_URL")).rstrip("/")
        self._prefix = (key_prefix or _env("R2_KEY_PREFIX") or DEFAULT_KEY_PREFIX)
        if not self._prefix.endswith("/"):
            self._prefix += "/"
        self._serve = (serve_mode or _env("MINUTES_R2_SERVE") or "presigned").lower()
        self._client = client  # cho phép inject (test); thật thì lazy-init

    # ----------------------------------------------------------------- client
    def _get_client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "s3",
                endpoint_url=self._endpoint,
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret,
                region_name="auto",
                config=Config(signature_version="s3v4"),
            )
        return self._client

    def _key(self, filename: str) -> str:
        """filename -> object key dưới prefix. Chống path traversal (chỉ lấy basename)."""
        return self._prefix + os.path.basename(filename)

    # ----------------------------------------------------------------- ops
    def upload(self, local_path: str, filename: str) -> bool:
        key = self._key(filename)
        try:
            self._get_client().upload_file(
                local_path,
                self._bucket,
                key,
                ExtraArgs={"ContentType": "audio/wav"},
            )
            logger.info(f"[storage:r2] Uploaded -> s3://{self._bucket}/{key}")
            return True
        except Exception as e:  # pragma: no cover - lỗi mạng/credential runtime
            logger.error(f"[storage:r2] Upload {key} thất bại: {e}")
            return False

    def exists(self, filename: str) -> bool:
        key = self._key(filename)
        try:
            self._get_client().head_object(Bucket=self._bucket, Key=key)
            return True
        except Exception:
            return False

    def url(self, filename: str) -> str:
        """URL phục vụ frontend: presigned (riêng tư) hoặc public (R2_PUBLIC_URL)."""
        key = self._key(filename)
        if self._serve == "public" and self._public_url:
            return f"{self._public_url}/{key}"
        return self.presigned_url(filename)

    def presigned_url(self, filename: str, expires: int = DEFAULT_PRESIGN_EXPIRES) -> str:
        key = self._key(filename)
        return self._get_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires,
        )

    def public_url(self, filename: str) -> str:
        return f"{self._public_url}/{self._key(filename)}"

    def delete(self, filename: str) -> bool:
        key = self._key(filename)
        try:
            self._get_client().delete_object(Bucket=self._bucket, Key=key)
            return True
        except Exception as e:  # pragma: no cover
            logger.error(f"[storage:r2] Xoá {key} thất bại: {e}")
            return False


# --------------------------------------------------------------------------- factory
_storage_singleton = None


def get_storage(recordings_dir: str = "recordings"):
    """Trả backend theo env ``MINUTES_STORAGE_BACKEND`` (cache singleton).

    "local" (mặc định) -> LocalStorage; "r2" -> R2Storage.
    """
    global _storage_singleton
    if _storage_singleton is not None:
        return _storage_singleton

    backend = _env("MINUTES_STORAGE_BACKEND", "local").lower()
    if backend == "r2":
        _storage_singleton = R2Storage()
        logger.info(
            f"[storage] Backend = R2 (bucket={_env('R2_BUCKET_NAME')}, "
            f"serve={_env('MINUTES_R2_SERVE') or 'presigned'})"
        )
    else:
        _storage_singleton = LocalStorage(recordings_dir=recordings_dir)
        if backend != "local":
            logger.warning(f"[storage] MINUTES_STORAGE_BACKEND={backend!r} lạ -> dùng local")
        else:
            logger.info("[storage] Backend = local")
    return _storage_singleton


def reset_storage_singleton():
    """Dùng cho test: xoá cache để get_storage() đọc lại env."""
    global _storage_singleton
    _storage_singleton = None
