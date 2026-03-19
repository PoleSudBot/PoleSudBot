from __future__ import annotations

from collections.abc import Sequence

from .model import MoeSekaiBinding, MoeSekaiBlacklistEntry, MoeSekaiUserSettings


async def get_or_create_user_settings(
    platform: str, user_id: str
) -> MoeSekaiUserSettings:
    settings, _ = await MoeSekaiUserSettings.get_or_create(
        platform=platform,
        user_id=user_id,
        defaults={"allow_share_profile": True},
    )
    return settings


async def get_user_settings(
    platform: str, user_id: str
) -> MoeSekaiUserSettings | None:
    return await MoeSekaiUserSettings.get_or_none(platform=platform, user_id=user_id)


async def list_user_bindings(platform: str, user_id: str) -> list[MoeSekaiBinding]:
    return (
        await MoeSekaiBinding.filter(platform=platform, user_id=user_id)
        .order_by("created_at", "id")
        .all()
    )


async def get_user_binding(
    platform: str, user_id: str, server: str
) -> MoeSekaiBinding | None:
    return await MoeSekaiBinding.get_or_none(
        platform=platform,
        user_id=user_id,
        server=server,
    )


async def upsert_user_binding(
    platform: str, user_id: str, server: str, game_id: str
) -> MoeSekaiBinding:
    binding, _ = await MoeSekaiBinding.update_or_create(
        platform=platform,
        user_id=user_id,
        server=server,
        defaults={"game_id": game_id},
    )
    return binding


async def delete_user_binding(
    platform: str, user_id: str, server: str
) -> MoeSekaiBinding | None:
    binding = await get_user_binding(platform, user_id, server)
    if binding:
        await binding.delete()
    return binding


async def set_default_server(
    platform: str, user_id: str, server: str | None
) -> MoeSekaiUserSettings:
    settings = await get_or_create_user_settings(platform, user_id)
    settings.default_server = server
    await settings.save(update_fields=["default_server", "updated_at"])
    return settings


async def set_allow_share_profile(
    platform: str, user_id: str, allow_share_profile: bool
) -> MoeSekaiUserSettings:
    settings = await get_or_create_user_settings(platform, user_id)
    settings.allow_share_profile = allow_share_profile
    await settings.save(update_fields=["allow_share_profile", "updated_at"])
    return settings


async def query_bindings_by_uid(
    game_id: str, server: str | None = None
) -> list[MoeSekaiBinding]:
    query = MoeSekaiBinding.filter(game_id=game_id)
    if server:
        query = query.filter(server=server)
    return await query.order_by("server", "created_at", "id").all()


async def add_blacklist_entry(
    target_type: str,
    target_value: str,
    created_by: str,
    server: str | None = None,
) -> MoeSekaiBlacklistEntry:
    entry, _ = await MoeSekaiBlacklistEntry.get_or_create(
        target_type=target_type,
        server=server,
        target_value=target_value,
        defaults={"created_by": created_by},
    )
    return entry


async def remove_blacklist_entry(
    target_type: str,
    target_value: str,
    server: str | None = None,
) -> bool:
    count = await MoeSekaiBlacklistEntry.filter(
        target_type=target_type,
        server=server,
        target_value=target_value,
    ).delete()
    return count > 0


async def get_blacklist_entry(
    target_type: str,
    target_value: str,
    server: str | None = None,
) -> MoeSekaiBlacklistEntry | None:
    return await MoeSekaiBlacklistEntry.get_or_none(
        target_type=target_type,
        server=server,
        target_value=target_value,
    )


async def list_blacklist_entries(
    target_type: str | None = None,
) -> list[MoeSekaiBlacklistEntry]:
    query = MoeSekaiBlacklistEntry.all()
    if target_type:
        query = query.filter(target_type=target_type)
    return await query.order_by("target_type", "server", "target_value").all()
