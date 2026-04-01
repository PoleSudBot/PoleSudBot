from __future__ import annotations

from ..config import get_storage_backend
from .base import RollpigStore


def build_store() -> RollpigStore:
    backend = get_storage_backend()
    if backend == "cloud":
        from .cloud import CloudStore

        return CloudStore()

    from ..data_manager import get_data_manager
    from .local_json import LocalJsonStore

    return LocalJsonStore(get_data_manager)


store = build_store()
