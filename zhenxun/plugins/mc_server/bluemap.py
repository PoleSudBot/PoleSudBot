from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urljoin

from .types import BlueMapPlayer


def parse_bluemap_players(payload: object, map_id: str) -> list[BlueMapPlayer]:
    if not isinstance(payload, dict):
        return []
    players = payload.get("players")
    if not isinstance(players, list):
        return []

    result: list[BlueMapPlayer] = []
    for item in players:
        if not isinstance(item, dict):
            continue
        position = item.get("position")
        if not isinstance(position, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        try:
            result.append(
                BlueMapPlayer(
                    uuid=str(item.get("uuid") or ""),
                    name=name,
                    map_id=map_id,
                    x=float(position.get("x")),
                    y=float(position.get("y")),
                    z=float(position.get("z")),
                )
            )
        except (TypeError, ValueError):
            continue
    return result


async def fetch_bluemap_players(
    base_url: str,
    map_ids: Iterable[str],
    *,
    timeout: int,
) -> list[BlueMapPlayer]:
    # AsyncHttpx 依赖 NoneBot driver，运行到实际抓取时再导入可让纯解析逻辑易于测试。
    from zhenxun.utils.http_utils import AsyncHttpx

    base = base_url.rstrip("/") + "/"
    result: list[BlueMapPlayer] = []
    for map_id in [item.strip("/") for item in map_ids if item.strip("/")]:
        # BlueMap 每个地图都有独立 live JSON，单个地图失败不影响其他地图展示。
        url = urljoin(base, f"maps/{map_id}/live/players.json")
        payload = await AsyncHttpx.get_json(url, default={}, timeout=timeout)
        result.extend(parse_bluemap_players(payload, map_id))
    return result
