from __future__ import annotations

import asyncio
from collections.abc import Callable

from .constants import DEFAULT_RCON_PORT
from .utils import parse_server_address


class McRconError(RuntimeError):
    pass


async def execute_rcon_command(
    address: str,
    password: str,
    command: str,
    *,
    timeout: int,
) -> str:
    if not password:
        raise McRconError("RCON密码未配置")
    parsed = parse_server_address(address, DEFAULT_RCON_PORT)
    return await asyncio.wait_for(
        asyncio.to_thread(_execute_sync, parsed.host, parsed.port, password, command),
        timeout=timeout,
    )


def _execute_sync(host: str, port: int, password: str, command: str) -> str:
    try:
        from mctools import RCONClient
    except ImportError as exc:
        raise McRconError("缺少依赖 mctools，请先同步项目依赖。") from exc

    client = RCONClient(host, port=port)
    try:
        logged_in = _call_with_optional_password(client.login, password)
        if logged_in is False:
            raise McRconError("RCON认证失败")
        response = client.command(command)
        return "" if response is None else str(response)
    except McRconError:
        raise
    except Exception as exc:
        raise McRconError(str(exc)) from exc
    finally:
        try:
            client.stop()
        except Exception:
            pass


def _call_with_optional_password(func: Callable, password: str) -> object:
    try:
        return func(password)
    except TypeError:
        return func()
