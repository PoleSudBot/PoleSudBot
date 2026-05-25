from __future__ import annotations

from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent
from nonebot_plugin_alconna import At, Match, Text, on_alconna
from nonebot_plugin_waiter import waiter

from zhenxun.utils.message import MessageUtils

from .command_schema import (
    first_at_from_parts,
    mcbind_command,
    mcbluemap_command,
    mcchart_command,
    mcinfo_command,
    mclist_command,
    mclog_command,
    mcrcon_command,
    mcsend_command,
    mctime_command,
    mctoggle_command,
    mcwhitelist_command,
    resolve_mctime_query,
    text_words_from_parts,
)
from .config import get_settings
from .services import mc_server_service
from .types import RenderedMessage
from .utils import is_bind_flow_done, is_bind_flow_exit, is_bind_flow_skip


async def _finish_rendered(rendered: RenderedMessage) -> None:
    await MessageUtils.build_message(rendered.to_message_parts()).finish(reply_to=True)


async def _send_rendered(rendered: RenderedMessage) -> None:
    await MessageUtils.build_message(rendered.to_message_parts()).send(reply_to=True)


def _require_group(event: Event) -> GroupMessageEvent | None:
    return event if isinstance(event, GroupMessageEvent) else None


def _match_words(match: Match[tuple[str, ...]]) -> list[str]:
    if not match.available:
        return []
    result = match.result
    if isinstance(result, str):
        return [result]
    return [str(item) for item in result]


def _join_words(words: list[str]) -> str:
    return " ".join(item for item in words if item).strip()


def _part_items(match: Match[tuple[Text | At, ...]]) -> tuple[Text | At, ...]:
    if not match.available:
        return ()
    result = match.result
    if isinstance(result, tuple):
        return result
    return (result,)


def _text_words_from_parts(parts: tuple[Text | At, ...]) -> list[str]:
    return text_words_from_parts(parts)


def _first_at_from_parts(parts: tuple[Text | At, ...]) -> str | None:
    return first_at_from_parts(parts)


def _sender_name(event: GroupMessageEvent) -> str:
    sender = event.sender
    return str(sender.card or sender.nickname or event.user_id)


async def _finish_text(message: str) -> None:
    await MessageUtils.build_message(message).finish(reply_to=True)


async def _send_text(message: str) -> None:
    await MessageUtils.build_message(message).send(reply_to=True)


def _bind_timeout_text(timeout: int) -> str:
    return f"绑定流程已超时（{timeout} 秒未收到回复），已结束。"


async def _run_bind_flow(group_event: GroupMessageEvent) -> None:
    group_id = str(group_event.group_id)
    user_id = str(group_event.user_id)
    timeout = get_settings().bind_flow_timeout_seconds

    @waiter(waits=["message"], keep_session=True)
    async def wait_reply(event: Event) -> str | None:
        reply_group_event = _require_group(event)
        if not reply_group_event:
            return None
        if (
            str(reply_group_event.group_id) != group_id
            or str(reply_group_event.user_id) != user_id
        ):
            return None
        return event.get_plaintext().strip()

    async def ask(prompt: str) -> str | None:
        reply = await wait_reply.wait(prompt, timeout=timeout)
        if reply is None:
            await _send_text(_bind_timeout_text(timeout))
        return reply

    await _send_text(
        "MC绑定向导已开始。\n"
        "流程会依次配置：服务器地址 -> 日志路径 -> RCON。\n"
        "任意步骤输入 q / quit / 退出 / 取消 可结束流程。"
    )

    # 地址是唯一必填项，探测成功前不写入数据库，避免把可用绑定覆盖成坏地址。
    while True:
        reply = await ask("请发送 MC 服务器地址，格式为 host[:port]，端口默认 25565。")
        if reply is None:
            return
        if is_bind_flow_exit(reply):
            await _send_text("已取消MC绑定流程。")
            return
        if not reply:
            await _send_text("服务器地址不能为空，请重新输入。")
            continue
        await _send_text("正在探测服务器状态，请稍候。")
        try:
            rendered = await mc_server_service.bind_group_server_with_status(
                group_id,
                reply,
            )
        except ValueError as exc:
            await _send_text(f"{exc}\n请重新输入服务器地址，或输入 q 结束流程。")
            continue
        await _send_rendered(rendered)
        break

    # 日志是可选事件源；skip 只跳过本次配置，不清空可能已有的旧日志路径。
    while True:
        reply = await ask(
            "请发送 Paper latest.log 文件路径，或 logs 目录路径。\n"
            "输入 skip 跳过日志配置。"
        )
        if reply is None:
            return
        if is_bind_flow_exit(reply):
            await _send_text("已取消MC绑定流程。")
            return
        if is_bind_flow_skip(reply):
            await _send_text("已跳过日志配置；如已有旧配置会继续保留。")
            break
        if not reply:
            await _send_text("日志路径不能为空，请重新输入，或输入 skip 跳过。")
            continue
        log_path = mc_server_service.resolve_log_path(reply)
        if not log_path:
            await _send_text(
                "日志文件不存在或不可读，请重新输入 latest.log 或 logs 目录。"
            )
            continue
        await _send_text(await mc_server_service.set_log_path(group_id, str(log_path)))
        break

    # RCON 地址可以先保存，但必须等私聊密码写入后再用 list 命令验证闭环。
    while True:
        reply = await ask(
            "请发送 RCON 地址，格式为 host[:port]，端口默认 25575。\n"
            "输入 skip 跳过 RCON 配置。"
        )
        if reply is None:
            return
        if is_bind_flow_exit(reply):
            await _send_text("已取消MC绑定流程。")
            return
        if is_bind_flow_skip(reply):
            await _send_text(
                "已跳过RCON配置。绑定流程完成，可用 mclist / mcinfo 检查。"
            )
            return
        if not reply:
            await _send_text("RCON地址不能为空，请重新输入，或输入 skip 跳过。")
            continue
        try:
            message = await mc_server_service.set_rcon_address(group_id, reply)
        except ValueError as exc:
            await _send_text(f"{exc}\n请重新输入RCON地址，或输入 skip 跳过。")
            continue
        await _send_text(
            f"{message}\n"
            f"请私聊Bot发送：mcrcon passwd {group_id} <RCON密码>\n"
            "设置完成后回本群输入 done，向导会执行 mcrcon list 验证。"
        )
        break

    # 密码只能私聊超级用户设置；群里只等待 done，防止 RCON 密码被误发到群内。
    while True:
        reply = await ask("私聊设置密码后请输入 done；输入 q 可结束流程。")
        if reply is None:
            return
        if is_bind_flow_exit(reply):
            await _send_text("已结束MC绑定流程。RCON密码可稍后继续私聊配置。")
            return
        if not is_bind_flow_done(reply):
            await _send_text("这里请只输入 done 触发验证，或输入 q 结束流程。")
            continue
        try:
            message = await mc_server_service.verify_group_rcon(group_id)
        except ValueError as exc:
            await _send_text(
                f"RCON验证失败：{exc}\n"
                "请私聊修正密码后再次输入 done，或输入 q 结束流程。"
            )
            continue
        await _send_text(f"{message}\n绑定流程完成，可用 mclist / mcinfo 检查。")
        return


mcbind = on_alconna(
    mcbind_command(),
    priority=5,
    block=True,
)
mclog = on_alconna(
    mclog_command(),
    priority=5,
    block=True,
)
mcbluemap = on_alconna(
    mcbluemap_command(),
    priority=5,
    block=True,
)
mclist = on_alconna(mclist_command(), priority=5, block=True)
mcinfo = on_alconna(
    mcinfo_command(),
    aliases={"mcstatus", "mci"},
    priority=5,
    block=True,
)
mctoggle = on_alconna(
    mctoggle_command(),
    aliases={"mctg"},
    priority=5,
    block=True,
)
mctime = on_alconna(
    mctime_command(),
    aliases={"mct"},
    priority=5,
    block=True,
)
mcchart = on_alconna(
    mcchart_command(),
    aliases={"mcc"},
    priority=5,
    block=True,
)
mcsend = on_alconna(
    mcsend_command(),
    aliases={"mcs"},
    priority=5,
    block=True,
)
mcrcon = on_alconna(
    mcrcon_command(),
    aliases={"mcr"},
    priority=5,
    block=True,
)
mcwhite = on_alconna(
    mcwhitelist_command(),
    aliases={"mcwhite", "mcw"},
    priority=5,
    block=True,
)


@mcbind.handle()
async def _(bot: Bot, event: Event, parts: Match[tuple[str, ...]]):
    group_event = _require_group(event)
    if not group_event:
        await _finish_text("请在群内绑定MC服务器。")
    if not await mc_server_service.ensure_admin(bot, event, str(group_event.group_id)):
        await _finish_text("需要群管理员或MC管理权限。")
    words = _match_words(parts)
    if not words:
        await _run_bind_flow(group_event)
        return
    if len(words) != 1:
        await _finish_text("用法：mcbind 或 mcbind <地址[:端口]>")
    address = words[0]
    try:
        rendered = await mc_server_service.bind_group_server_with_status(
            str(group_event.group_id),
            address,
        )
    except ValueError as exc:
        await _finish_text(str(exc))
    await _finish_rendered(rendered)


@mclog.handle()
async def _(bot: Bot, event: Event, path: Match[tuple[str, ...]]):
    group_event = _require_group(event)
    if not group_event:
        await _finish_text("请在群内设置日志路径。")
    if not await mc_server_service.ensure_admin(bot, event, str(group_event.group_id)):
        await _finish_text("需要群管理员或MC管理权限。")
    raw_path = _join_words(_match_words(path))
    if not raw_path:
        await _finish_text("用法：mclog <latest.log路径或logs目录>")
    message = await mc_server_service.set_log_path(str(group_event.group_id), raw_path)
    await _finish_text(message)


@mcbluemap.handle()
async def _(bot: Bot, event: Event, parts: Match[tuple[str, ...]]):
    group_event = _require_group(event)
    if not group_event:
        await _finish_text("请在群内设置BlueMap。")
    if not await mc_server_service.ensure_admin(bot, event, str(group_event.group_id)):
        await _finish_text("需要群管理员或MC管理权限。")
    words = _match_words(parts)
    if len(words) < 2:
        await _finish_text("用法：mcbluemap <base_url> <map_id...>")
    message = await mc_server_service.set_bluemap(
        str(group_event.group_id),
        words[0],
        words[1:],
    )
    await _finish_text(message)


@mclist.handle()
async def _(event: Event):
    group_event = _require_group(event)
    if not group_event:
        await _finish_text("请在群内查看MC绑定状态。")
    message = await mc_server_service.list_config(str(group_event.group_id))
    await _finish_text(message)


@mcinfo.handle()
async def _(event: Event):
    group_event = _require_group(event)
    if not group_event:
        await _finish_text("请在群内查询MC服务器。")
    try:
        await _finish_rendered(
            await mc_server_service.status_message(str(group_event.group_id))
        )
    except ValueError as exc:
        await _finish_text(str(exc))


@mctoggle.handle()
async def _(bot: Bot, event: Event, parts: Match[tuple[str, ...]]):
    group_event = _require_group(event)
    if not group_event:
        await _finish_text("请在群内修改MC开关。")
    if not await mc_server_service.ensure_admin(bot, event, str(group_event.group_id)):
        await _finish_text("需要群管理员或MC管理权限。")
    words = [item.lower() for item in _match_words(parts)]
    if len(words) != 2 or words[1] not in {"on", "off"}:
        await _finish_text("用法：mctoggle join|conn|chat|all on|off")
    message = await mc_server_service.toggle(
        str(group_event.group_id),
        words[0],
        enabled=words[1] == "on",
    )
    await _finish_text(message)


@mctime.handle()
async def _(event: Event, parts: Match[tuple[Text | At, ...]]):
    group_event = _require_group(event)
    if not group_event:
        await _finish_text("请在群内查询在线时长。")
    arg_parts = _part_items(parts)
    qq_id, range_text = resolve_mctime_query(
        arg_parts,
        self_qq_id=str(group_event.user_id),
    )
    try:
        if qq_id:
            rendered = await mc_server_service.personal_online_message(
                str(group_event.group_id),
                range_text,
                qq_id=qq_id,
            )
        else:
            rendered = await mc_server_service.playtime_message(
                str(group_event.group_id),
                range_text,
            )
        await _finish_rendered(rendered)
    except ValueError as exc:
        await _finish_text(str(exc))


@mcchart.handle()
async def _(event: Event, range_parts: Match[tuple[str, ...]]):
    group_event = _require_group(event)
    if not group_event:
        await _finish_text("请在群内查询人数图。")
    range_text = _join_words(_match_words(range_parts)) or None
    try:
        await _finish_rendered(
            await mc_server_service.chart_message(
                str(group_event.group_id),
                range_text,
            )
        )
    except ValueError as exc:
        await _finish_text(str(exc))


@mcsend.handle()
async def _(event: Event, message_parts: Match[tuple[str, ...]]):
    group_event = _require_group(event)
    if not group_event:
        await _finish_text("请在群内发送MC消息。")
    message = _join_words(_match_words(message_parts))
    if not message:
        await _finish_text("用法：mcsend <消息>")
    try:
        result = await mc_server_service.send_chat_to_game(
            str(group_event.group_id), _sender_name(group_event), message
        )
    except ValueError as exc:
        result = str(exc)
    if result is None:
        return
    await _finish_text(result)


@mcrcon.handle()
async def _(bot: Bot, event: Event, parts: Match[tuple[str, ...]]):
    words = _match_words(parts)
    if isinstance(event, PrivateMessageEvent) and words[:1] == ["passwd"]:
        if len(words) < 3:
            await _finish_text("用法：mcrcon passwd <群号> <密码>")
        from nonebot.permission import SUPERUSER

        if not await SUPERUSER(bot, event):
            await _finish_text("该指令仅超级用户可用。")
        try:
            message = await mc_server_service.set_rcon_password(
                words[1],
                _join_words(words[2:]),
            )
        except ValueError as exc:
            message = str(exc)
        await _finish_text(message)

    group_event = _require_group(event)
    if not group_event:
        await _finish_text("RCON执行与地址配置请在群内使用。")
    if words[:1] == ["set"]:
        if not await mc_server_service.ensure_admin(
            bot,
            event,
            str(group_event.group_id),
        ):
            await _finish_text("需要群管理员或MC管理权限。")
        address = _join_words(words[1:])
        if not address:
            await _finish_text("用法：mcrcon set <host:port>")
        try:
            message = await mc_server_service.set_rcon_address(
                str(group_event.group_id),
                address,
            )
        except ValueError as exc:
            message = str(exc)
        await _finish_text(message)

    text = _join_words(words)
    if not text:
        await _finish_text("用法：mcrcon <命令> / mcrcon set <host:port>")
    if not await mc_server_service.ensure_rcon_permission(
        bot,
        event,
        str(group_event.group_id),
    ):
        await _finish_text("RCON权限不足。")
    try:
        result = await mc_server_service.execute_group_rcon(
            str(group_event.group_id),
            text,
        )
    except ValueError as exc:
        result = str(exc)
    await _finish_text(result)


@mcwhite.handle()
async def _(bot: Bot, event: Event, parts: Match[tuple[Text | At, ...]]):
    group_event = _require_group(event)
    if not group_event:
        await _finish_text("请在群内添加白名单。")
    arg_parts = _part_items(parts)
    text_words = _text_words_from_parts(arg_parts)
    player_name = text_words[0] if text_words else ""
    if not player_name:
        await _finish_text("用法：mcwhitelist <玩家名> [@用户]")

    target_qq = _first_at_from_parts(arg_parts) or str(group_event.user_id)
    if target_qq != str(group_event.user_id):
        if not await mc_server_service.ensure_admin(
            bot,
            event,
            str(group_event.group_id),
        ):
            await _finish_text("代绑需要群管理员或MC管理权限。")
    try:
        result = await mc_server_service.whitelist(
            str(group_event.group_id),
            player_name,
            qq_id=target_qq,
            created_by=str(group_event.user_id),
        )
    except ValueError as exc:
        result = str(exc)
    await _finish_text(result)
