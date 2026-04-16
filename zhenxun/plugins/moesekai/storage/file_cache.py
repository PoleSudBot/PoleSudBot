from __future__ import annotations

from hashlib import sha256
from pathlib import Path, PurePosixPath
import time

from ..adapters.runtime import logger
from ..constants import MODULE_NAME


class BinaryFileCacheStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path_for_key(self, key: str, *, suffix: str) -> Path:
        hashed = sha256(key.encode("utf-8")).hexdigest()
        normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
        return self.root / f"{hashed}{normalized_suffix}"

    def load(
        self,
        key: str,
        *,
        ttl_seconds: int,
        suffix: str = ".bin",
    ) -> bytes | None:
        path = self._path_for_key(key, suffix=suffix)
        if not path.exists():
            return None
        if ttl_seconds > 0:
            age = time.time() - path.stat().st_mtime
            if age > ttl_seconds:
                path.unlink(missing_ok=True)
                return None
        try:
            return path.read_bytes()
        except OSError as exc:
            logger.warning(
                f"MoeSekai 文件缓存读取失败: {path.name}",
                MODULE_NAME,
                e=exc,
            )
            return None

    def save(self, key: str, payload: bytes, *, suffix: str = ".bin") -> Path:
        path = self._path_for_key(key, suffix=suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(f"{path.suffix}.tmp")
        tmp_path.write_bytes(payload)
        tmp_path.replace(path)
        return path

    def delete(self, key: str, *, suffix: str = ".bin") -> None:
        path = self._path_for_key(key, suffix=suffix)
        path.unlink(missing_ok=True)


class PathBinaryFileStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _resolve_path(self, relative_path: str) -> Path:
        normalized = PurePosixPath(str(relative_path).strip("/"))
        if not normalized.parts:
            raise ValueError("relative_path 不能为空")
        if any(part in {"..", "."} for part in normalized.parts):
            raise ValueError(f"relative_path 非法: {relative_path}")
        return self.root.joinpath(*normalized.parts)

    def resolve_path(self, relative_path: str) -> Path:
        return self._resolve_path(relative_path)

    def load(
        self,
        relative_path: str,
        *,
        ttl_seconds: int = 0,
    ) -> bytes | None:
        path = self._resolve_path(relative_path)
        if not path.exists():
            return None
        if ttl_seconds > 0:
            age = time.time() - path.stat().st_mtime
            if age > ttl_seconds:
                path.unlink(missing_ok=True)
                return None
        try:
            return path.read_bytes()
        except OSError as exc:
            logger.warning(
                f"MoeSekai 文件缓存读取失败: {path.name}",
                MODULE_NAME,
                e=exc,
            )
            return None

    def save(self, relative_path: str, payload: bytes) -> Path:
        path = self._resolve_path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(f"{path.suffix}.tmp")
        tmp_path.write_bytes(payload)
        tmp_path.replace(path)
        return path

    def delete(self, relative_path: str) -> None:
        path = self._resolve_path(relative_path)
        path.unlink(missing_ok=True)
