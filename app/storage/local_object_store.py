"""本地对象存储：把原始文件落到本地目录。

原文必须留档 —— 引用溯源要能跳回原文，解析器换版本后也要能重跑。
离线单机没有 MinIO，但留档这件事不能省。

通过 ``OBJECT_BACKEND=local`` 启用。生产仍然是 MinIO。
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


class LocalObjectStore:
    """文件系统版对象存储。接口与 :class:`app.storage.object_store.ObjectStore` 一致。"""

    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or settings.local_store_dir) / "objects"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # key 形如 "<doc_id>/<hash12>/<filename>"，天然带目录层级
        return self.root / key

    def ensure_bucket(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise FileNotFoundError(f"对象不存在: {key}")
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    def close(self) -> None:
        return None


def copy_tree(src: str, dst: str) -> None:
    shutil.copytree(src, dst, dirs_exist_ok=True)
