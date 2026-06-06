from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import RollpigStore

__all__ = ["store"]


def __getattr__(name: str) -> "RollpigStore":
    if name != "store":
        raise AttributeError(name)

    # 只在真正访问存储实例时构建后端，避免导入 store.models 时触发配置初始化。
    from .factory import store

    globals()["store"] = store
    return store
