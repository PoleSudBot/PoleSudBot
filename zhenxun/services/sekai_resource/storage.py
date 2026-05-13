from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import time
from typing import Any
from uuid import uuid4

from .constants import MODULE_NAME
from .runtime import logger


class JsonStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self, default: Any) -> Any:
        if not self.path.exists():
            return default
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning(
                f"SekaiResource 状态文件损坏，将回退默认值: {self.path.name}",
                MODULE_NAME,
                e=exc,
            )
            return default

    def save(self, payload: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 状态文件先写临时文件再 replace，避免进程中断留下半截 JSON。
        tmp_path = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(self.path)
        except OSError:
            tmp_path.unlink(missing_ok=True)
            raise


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
                f"SekaiResource 文件缓存读取失败: {path.name}",
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
