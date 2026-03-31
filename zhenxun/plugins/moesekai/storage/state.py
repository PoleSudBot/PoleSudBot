from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..adapters.runtime import logger
from ..constants import MODULE_NAME


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
                f"MoeSekai 状态文件损坏，将回退默认值: {self.path.name}",
                MODULE_NAME,
                e=exc,
            )
            return default

    def save(self, payload: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
