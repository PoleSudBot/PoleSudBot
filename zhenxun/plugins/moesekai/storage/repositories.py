from __future__ import annotations

from collections.abc import Sequence

from ..constants import normalize_alias
from ..model import (
    MoeSekaiAliasEntry,
    MoeSekaiBinding,
    MoeSekaiBlacklistEntry,
    MoeSekaiGroupFeatureToggle,
    MoeSekaiNotificationRecord,
    MoeSekaiUserFeatureSubscription,
    MoeSekaiUserSettings,
)


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


async def upsert_alias_entry(
    *,
    target_type: str,
    target_value: str,
    alias: str,
    scope: str,
    created_by: str,
) -> MoeSekaiAliasEntry:
    entry, _ = await MoeSekaiAliasEntry.update_or_create(
        target_type=target_type,
        alias_normalized=normalize_alias(alias),
        scope=scope,
        defaults={
            "target_value": target_value,
            "alias": alias,
            "created_by": created_by,
        },
    )
    return entry


async def remove_alias_entry(
    *,
    target_type: str,
    alias: str,
    scope: str,
) -> bool:
    count = await MoeSekaiAliasEntry.filter(
        target_type=target_type,
        alias_normalized=normalize_alias(alias),
        scope=scope,
    ).delete()
    return count > 0


async def get_alias_entry(
    *,
    target_type: str,
    alias: str,
    scope: str,
) -> MoeSekaiAliasEntry | None:
    return await MoeSekaiAliasEntry.get_or_none(
        target_type=target_type,
        alias_normalized=normalize_alias(alias),
        scope=scope,
    )


async def resolve_alias(
    *,
    target_type: str,
    alias: str,
    scopes: Sequence[str],
) -> MoeSekaiAliasEntry | None:
    if not scopes:
        return None
    entries = await MoeSekaiAliasEntry.filter(
        target_type=target_type,
        alias_normalized=normalize_alias(alias),
        scope__in=list(scopes),
    ).all()
    if not entries:
        return None
    order = {scope: index for index, scope in enumerate(scopes)}
    entries.sort(key=lambda item: order.get(item.scope, len(order)))
    return entries[0]


async def list_alias_entries(
    *,
    target_type: str,
    scope: str | None = None,
    target_value: str | None = None,
) -> list[MoeSekaiAliasEntry]:
    query = MoeSekaiAliasEntry.filter(target_type=target_type)
    if scope is not None:
        query = query.filter(scope=scope)
    if target_value is not None:
        query = query.filter(target_value=target_value)
    return await query.order_by("scope", "created_at", "id").all()


async def search_alias_entries(
    *,
    target_type: str,
    keyword: str,
    scopes: Sequence[str] | None = None,
    limit: int = 20,
) -> list[MoeSekaiAliasEntry]:
    query = MoeSekaiAliasEntry.filter(target_type=target_type)
    if scopes:
        query = query.filter(scope__in=list(scopes))
    normalized = normalize_alias(keyword)
    entries = await query.order_by("scope", "alias").limit(limit * 4).all()
    filtered = [
        item
        for item in entries
        if normalized in item.alias_normalized
        or normalized == item.target_value
        or keyword.strip() in item.alias
    ]
    return filtered[:limit]


async def set_group_feature_toggle(
    *,
    platform: str,
    group_id: str,
    feature_name: str,
    server: str,
    enabled: bool,
) -> MoeSekaiGroupFeatureToggle:
    toggle, _ = await MoeSekaiGroupFeatureToggle.update_or_create(
        platform=platform,
        group_id=group_id,
        feature_name=feature_name,
        server=server,
        defaults={"enabled": enabled},
    )
    return toggle


async def get_group_feature_toggle(
    *,
    platform: str,
    group_id: str,
    feature_name: str,
    server: str,
) -> MoeSekaiGroupFeatureToggle | None:
    return await MoeSekaiGroupFeatureToggle.get_or_none(
        platform=platform,
        group_id=group_id,
        feature_name=feature_name,
        server=server,
    )


async def list_enabled_group_feature_toggles(
    *,
    feature_name: str,
    server: str | None = None,
) -> list[MoeSekaiGroupFeatureToggle]:
    query = MoeSekaiGroupFeatureToggle.filter(feature_name=feature_name, enabled=True)
    if server:
        query = query.filter(server=server)
    return await query.order_by("platform", "group_id").all()


async def upsert_user_feature_subscription(
    *,
    platform: str,
    group_id: str,
    user_id: str,
    feature_name: str,
    server: str,
) -> MoeSekaiUserFeatureSubscription:
    subscription, _ = await MoeSekaiUserFeatureSubscription.update_or_create(
        platform=platform,
        group_id=group_id,
        user_id=user_id,
        feature_name=feature_name,
        server=server,
    )
    return subscription


async def remove_user_feature_subscription(
    *,
    platform: str,
    group_id: str,
    user_id: str,
    feature_name: str,
    server: str,
) -> bool:
    count = await MoeSekaiUserFeatureSubscription.filter(
        platform=platform,
        group_id=group_id,
        user_id=user_id,
        feature_name=feature_name,
        server=server,
    ).delete()
    return count > 0


async def list_user_feature_subscriptions(
    *,
    platform: str,
    group_id: str,
    feature_name: str,
    server: str,
) -> list[MoeSekaiUserFeatureSubscription]:
    return (
        await MoeSekaiUserFeatureSubscription.filter(
            platform=platform,
            group_id=group_id,
            feature_name=feature_name,
            server=server,
        )
        .order_by("user_id")
        .all()
    )


async def get_user_feature_subscription(
    *,
    platform: str,
    group_id: str,
    user_id: str,
    feature_name: str,
    server: str,
) -> MoeSekaiUserFeatureSubscription | None:
    return await MoeSekaiUserFeatureSubscription.get_or_none(
        platform=platform,
        group_id=group_id,
        user_id=user_id,
        feature_name=feature_name,
        server=server,
    )


async def has_notification_record(
    *,
    platform: str,
    group_id: str,
    feature_name: str,
    server: str,
    record_key: str,
) -> bool:
    return (
        await MoeSekaiNotificationRecord.filter(
            platform=platform,
            group_id=group_id,
            feature_name=feature_name,
            server=server,
            record_key=record_key,
        ).exists()
    )


async def list_notification_record_keys_by_groups(
    *,
    feature_name: str,
    server: str,
    record_key_prefix: str,
    groups: list[tuple[str, str]],
) -> dict[tuple[str, str], set[str]]:
    result = {(platform, group_id): set() for platform, group_id in groups}
    if not result:
        return result
    # 这里按前缀一次性拉回本轮记录，避免群发送阶段对 notification_records 做 N+1 exists 查询。
    records = await MoeSekaiNotificationRecord.filter(
        feature_name=feature_name,
        server=server,
        record_key__startswith=record_key_prefix,
    ).values_list("platform", "group_id", "record_key")
    allowed_groups = set(result)
    for platform, group_id, record_key in records:
        group_key = (platform, group_id)
        if group_key in allowed_groups:
            result[group_key].add(record_key)
    return result


async def create_notification_record(
    *,
    platform: str,
    group_id: str,
    feature_name: str,
    server: str,
    record_key: str,
) -> MoeSekaiNotificationRecord:
    record, _ = await MoeSekaiNotificationRecord.get_or_create(
        platform=platform,
        group_id=group_id,
        feature_name=feature_name,
        server=server,
        record_key=record_key,
    )
    return record
