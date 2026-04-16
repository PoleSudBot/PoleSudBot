import asyncio
import re

import aiofiles
from arclet.alconna import Alconna, Args, Arparma, MultiVar, Option, Subcommand
from nonebot.adapters.onebot.v11 import Bot, Event
from nonebot.exception import ActionFailed, AdapterException
from nonebot.typing import T_State
from nonebot_plugin_alconna import At, on_alconna
from nonebot_plugin_alconna.uniseg import Image, UniMessage

from zhenxun.services.log import logger
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.platform import PlatformUtils

from ..config import (
    safe_file_exists,
    resolve_quote_image_path,
)
from ..model import Quote
from ..services.quote_service import QuoteService

_LIAN_COUNT_PATTERN = re.compile(r"^(?P<count>(?:10|[1-9]|[一二两三四五六七八九十]))连$")
_SUFFIX_LIAN_COUNT_PATTERN = re.compile(
    r"^(?P<keyword>.+?)(?P<count>(?:10|[1-9]|[一二两三四五六七八九十]))连$"
)
_CN_LIAN_COUNT_MAP = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

quote_alc = Alconna(
    "语录",
    Option("-n|--num", Args["count", int, 1], help_text="一次获取的语录数量"),
    Args["target_user?", At]["search_keywords?", MultiVar(str)],
)
record_pool = on_alconna(quote_alc, priority=2, block=True)
# 不能直接给“语录”开 compact=True，否则会吞掉“语录统计/语录主题/语录管理”。
# 这里用负前瞻只补“语录xxx”这种普通查询粘连写法，并保留管理/统计命令原路由。
record_pool.shortcut(
    r"语录(?!(?:统计|主题|管理)(?:\s|$))(?P<query>.+)",
    {"args": ["{query}"], "fuzzy": False},
)

stats_alc = Alconna(
    "quote",
    Subcommand(
        "stats",
        Subcommand("hot", Args["limit?", int, 10], alias={"热门"}),
        Subcommand("top-uploaders", Args["limit?", int, 10], alias={"高产上传"}),
        Subcommand("top-quoted", Args["limit?", int, 10], alias={"高产被录"}),
    ),
)
quote_stats_cmd = on_alconna(stats_alc, priority=5, block=True)
quote_stats_cmd.shortcut("语录统计", {"args": ["stats"]})


def _normalize_quote_count(count: int | None) -> int:
    if count is None:
        return 1
    return max(1, min(int(count), 10))


def _parse_lian_count(token: str) -> int | None:
    stripped = token.strip()
    if not stripped:
        return None
    if stripped.isdigit():
        return _normalize_quote_count(int(stripped))
    return _CN_LIAN_COUNT_MAP.get(stripped)


def _extract_quote_query_and_count(
    search_keywords: list[str], option_count: int | None
) -> tuple[str, int]:
    normalized_keywords = [
        keyword.strip() for keyword in search_keywords if str(keyword).strip()
    ]
    if option_count is not None:
        # 显式 -n/--num 的优先级最高，避免“语录 -n 1 五连”这类写法被误拆成数量请求。
        return " ".join(normalized_keywords), _normalize_quote_count(option_count)

    if not normalized_keywords:
        return "", 1

    if len(normalized_keywords) == 1:
        token = normalized_keywords[0]
        if match := _LIAN_COUNT_PATTERN.fullmatch(token):
            return "", _parse_lian_count(match.group("count")) or 1
        if match := _SUFFIX_LIAN_COUNT_PATTERN.fullmatch(token):
            # 仅支持“整条 X连”或“末尾 X连”，故意不支持任意位置数量写法，
            # 这样可以把歧义控制在最小范围，避免把正常关键词误解释成数量。
            return (
                match.group("keyword").strip(),
                _parse_lian_count(match.group("count")) or 1,
            )

    last_token = normalized_keywords[-1]
    if match := _LIAN_COUNT_PATTERN.fullmatch(last_token):
        return (
            " ".join(normalized_keywords[:-1]).strip(),
            _parse_lian_count(match.group("count")) or 1,
        )

    return " ".join(normalized_keywords), 1


def _build_random_memory_key(group_id: str, user_id_filter: str | None = None) -> str:
    return f"{group_id}_{user_id_filter or 'all'}"


def _build_search_memory_key(
    group_id: str, keyword: str, user_id_filter: str | None = None
) -> str:
    return f"{group_id}_{user_id_filter or 'all'}_{keyword}"


def _build_multi_quote_message(quotes: list[Quote]) -> UniMessage:
    message = UniMessage()
    for quote in quotes:
        # 普通多图发送直接走本地路径，避免 1-5 张场景额外把所有图片读成字节占内存。
        message += Image(path=resolve_quote_image_path(quote.image_path))
    return message


async def _delete_invalid_quotes(invalid_quote_ids: list[int]) -> None:
    if not invalid_quote_ids:
        return

    unique_invalid_ids = list(dict.fromkeys(invalid_quote_ids))
    logger.warning(
        f"检测到 {len(unique_invalid_ids)} 条语录文件缺失，将批量删除无效记录: {unique_invalid_ids}",
        "群聊语录",
    )
    await Quote.filter(id__in=unique_invalid_ids).delete()


async def _collect_valid_quotes_from_candidates(
    memory_key: str, candidate_quotes: list[Quote], target_count: int
) -> list[Quote]:
    valid_quotes: list[Quote] = []
    invalid_quote_ids: list[int] = []
    remaining_candidates = list(candidate_quotes)

    while remaining_candidates and len(valid_quotes) < target_count:
        # 每轮多拿 5 条候选，是为了在存在坏文件时尽量一次补齐，而不是频繁重跑检索。
        batch_size = min(len(remaining_candidates), (target_count - len(valid_quotes)) + 5)
        provisional_quotes = QuoteService.select_quotes_without_record(
            memory_key, remaining_candidates, batch_size
        )
        if not provisional_quotes:
            break

        provisional_ids = {quote.id for quote in provisional_quotes}
        remaining_candidates = [
            quote for quote in remaining_candidates if quote.id not in provisional_ids
        ]

        for quote in provisional_quotes:
            if safe_file_exists(quote.image_path):
                if len(valid_quotes) < target_count:
                    valid_quotes.append(quote)
            else:
                # 坏记录先集中回收，避免在循环里边查边删导致额外数据库抖动。
                logger.warning(
                    f"数据库中的语录 (ID: {quote.id}) 对应的图片文件不存在: {quote.image_path}",
                    "群聊语录",
                )
                invalid_quote_ids.append(quote.id)

    await _delete_invalid_quotes(invalid_quote_ids)
    return valid_quotes


async def _collect_valid_quotes_from_random_ids(
    memory_key: str, candidate_ids: list[int], target_count: int
) -> list[Quote]:
    valid_quotes: list[Quote] = []
    invalid_quote_ids: list[int] = []
    remaining_ids = list(candidate_ids)

    while remaining_ids and len(valid_quotes) < target_count:
        batch_size = min(len(remaining_ids), (target_count - len(valid_quotes)) + 5)
        provisional_ids = QuoteService.select_quote_ids_without_record(
            memory_key, remaining_ids, batch_size
        )
        if not provisional_ids:
            break

        provisional_id_set = set(provisional_ids)
        remaining_ids = [
            quote_id for quote_id in remaining_ids if quote_id not in provisional_id_set
        ]
        provisional_quotes = await QuoteService.get_quotes_by_ids_in_order(
            provisional_ids
        )

        for quote in provisional_quotes:
            if safe_file_exists(quote.image_path):
                if len(valid_quotes) < target_count:
                    valid_quotes.append(quote)
            else:
                logger.warning(
                    f"数据库中的语录 (ID: {quote.id}) 对应的图片文件不存在: {quote.image_path}",
                    "群聊语录",
                )
                invalid_quote_ids.append(quote.id)

    await _delete_invalid_quotes(invalid_quote_ids)
    return valid_quotes


async def _get_valid_random_quotes(
    group_id: str,
    target_count: int,
    user_id_filter: str | None = None,
) -> tuple[list[Quote], str]:
    memory_key = _build_random_memory_key(group_id, user_id_filter)

    if user_id_filter:
        # 用户筛选同时依赖 quoted_user_id 和 user:<qq> tag，继续保留 Python 层过滤以保持现有语义。
        random_candidates = [
            quote
            for quote in await Quote.filter(group_id=group_id)
            if QuoteService._match_user_filter(quote, user_id_filter)
        ]
        return (
            await _collect_valid_quotes_from_candidates(
                memory_key, random_candidates, target_count
            ),
            memory_key,
        )

    random_quote_ids = await QuoteService.get_random_quote_ids(group_id)
    return (
        await _collect_valid_quotes_from_random_ids(
            memory_key, random_quote_ids, target_count
        ),
        memory_key,
    )


async def _send_quote_batch(
    bot: Bot,
    target,
    group_id: str,
    quotes: list[Quote],
) -> bool:
    if len(quotes) <= 5:
        await _build_multi_quote_message(quotes).send(target=target, bot=bot)
        return True

    forward_messages: list[UniMessage] = []
    for quote in quotes:
        absolute_path = resolve_quote_image_path(quote.image_path)
        async with aiofiles.open(absolute_path, "rb") as file:
            image_bytes = await file.read()
        forward_messages.append(UniMessage([Image(raw=image_bytes)]))

    try:
        await bot.call_api(
            "send_group_forward_msg",
            group_id=int(group_id),
            messages=MessageUtils.template2forward(forward_messages, bot.self_id),
        )
        return True
    except (ActionFailed, AdapterException, asyncio.TimeoutError) as e:
        logger.error(
            f"发送语录合并转发失败 - 群组: {group_id}, 数量: {len(quotes)}, 错误: {e}",
            "群聊语录",
            e=e,
        )
        await MessageUtils.build_message("合并转发发送失败，请稍后重试。").send(
            target=target, bot=bot
        )
        return False


@record_pool.handle()
async def record_pool_handle(bot: Bot, event: Event, arp: Arparma, state: T_State):
    """语录查询处理函数。"""
    session_id = event.get_session_id()
    if "group" not in session_id:
        return

    group_id = session_id.split("_")[1]
    target = PlatformUtils.get_target(group_id=group_id)

    at_user_info: At | None = arp.all_matched_args.get("target_user")
    search_keywords: list[str] = arp.all_matched_args.get("search_keywords", [])
    search_key_processed, request_count = _extract_quote_query_and_count(
        search_keywords, arp.query("num.count")
    )
    user_id_filter: str | None = str(at_user_info.target) if at_user_info else None

    quotes: list[Quote] = []
    memory_key: str | None = None
    fallback_message: UniMessage | None = None

    if search_key_processed:
        search_memory_key = _build_search_memory_key(
            group_id, search_key_processed, user_id_filter
        )
        matched_quotes = await QuoteService.search_quotes(
            group_id, search_key_processed, user_id_filter
        )
        quotes = await _collect_valid_quotes_from_candidates(
            search_memory_key, matched_quotes, request_count
        )
        if quotes:
            memory_key = search_memory_key
        else:
            # 只有关键词有效命中为 0 时才回退随机，命中了但数量不足时只发命中的结果，
            # 避免把“查询结果”悄悄掺入无关随机图，破坏搜索语义。
            quotes, memory_key = await _get_valid_random_quotes(
                group_id,
                request_count,
                user_id_filter=user_id_filter,
            )
            if quotes:
                if user_id_filter:
                    count_text = f"{len(quotes)} 条" if len(quotes) > 1 else "一条"
                    fallback_message = MessageUtils.build_message(
                        [
                            At(target=user_id_filter, flag="user"),
                            f" 关于 '{search_key_processed}' 的语录没找到哦，这是TA的{count_text}随机语录：\n",
                        ]
                    )
                else:
                    fallback_message = MessageUtils.build_message(
                        [f"当前查询无结果, 为您随机发送 {len(quotes)} 条语录。"]
                    )
            elif user_id_filter:
                await MessageUtils.build_message(
                    [At(target=user_id_filter, flag="user"), " 没有任何语录哦~"]
                ).send(target=target, bot=bot)
                return
            else:
                await MessageUtils.build_message("当前无语录库").send(
                    target=target, bot=bot
                )
                return
    else:
        quotes, memory_key = await _get_valid_random_quotes(
            group_id, request_count, user_id_filter=user_id_filter
        )
        if not quotes:
            if user_id_filter:
                await MessageUtils.build_message(
                    [At(target=user_id_filter, flag="user"), " 没有任何语录哦~"]
                ).send(target=target, bot=bot)
            else:
                await MessageUtils.build_message("当前无语录库").send(
                    target=target, bot=bot
                )
            return

    if fallback_message:
        await fallback_message.send(target=target, bot=bot)

    if not await _send_quote_batch(bot, target, group_id, quotes):
        return

    if memory_key:
        QuoteService.record_recent_quote_ids(memory_key, [quote.id for quote in quotes])
    await QuoteService.increment_view_counts([quote.id for quote in quotes])


@quote_stats_cmd.handle()
async def handle_quote_stats(bot: Bot, event: Event, arp: Arparma):
    """语录统计处理函数"""
    session_id = event.get_session_id()
    current_user_id = str(event.get_user_id())

    group_id_to_query: str | None = None

    if "group" in session_id:
        group_id_to_query = session_id.split("_")[1]

    if not group_id_to_query:
        await quote_stats_cmd.finish("请在群聊中执行此命令。")
        return

    reply_target = PlatformUtils.get_target(
        group_id=session_id.split("_")[1] if "group" in session_id else None,
        user_id=current_user_id if "private" in session_id else None,
    )
    if not reply_target:
        reply_target = PlatformUtils.get_target(user_id=current_user_id)

    result_message: str | bytes | None = None

    path_to_check = ""
    if arp.find("stats.hot"):
        path_to_check = "hot"
    elif arp.find("stats.top-uploaders"):
        path_to_check = "top-uploaders"
    elif arp.find("stats.top-quoted"):
        path_to_check = "top-quoted"

    if path_to_check == "hot":
        limit = arp.query("stats.hot.limit", 10)
        logger.debug(f"执行 '热门' 统计，limit={limit}", "群聊语录")

        import time

        start_time = time.time()

        hottest_quotes = await QuoteService.get_hottest_quotes(group_id_to_query, limit)

        fetch_time = time.time() - start_time
        logger.debug(f"获取热门语录耗时: {fetch_time:.3f}秒", "群聊语录")

        if hottest_quotes:
            image_quotes_count = sum(
                1
                for q in hottest_quotes
                if q.image_path and not (q.ocr_text or q.recorded_text)
            )
            text_quotes_count = len(hottest_quotes) - image_quotes_count
            logger.debug(
                f"热门语录类型统计 - 总数: {len(hottest_quotes)}, "
                f"图片语录: {image_quotes_count}, 文本语录: {text_quotes_count}",
                "群聊语录",
            )

            gen_start_time = time.time()

            result_message = await QuoteService.generate_hottest_quotes_image(
                group_id_to_query, hottest_quotes, bot.self_id
            )

            gen_time = time.time() - gen_start_time
            logger.debug(f"生成热门语录图片耗时: {gen_time:.3f}秒", "群聊语录")

            total_time = time.time() - start_time
            logger.debug(f"热门语录统计总耗时: {total_time:.3f}秒", "群聊语录")
        else:
            logger.warning(f"群组 {group_id_to_query} 没有热门语录数据", "群聊语录")
            result_message = f"群组 {group_id_to_query} 暂时没有热门语录。"

    elif path_to_check == "top-uploaders":
        limit = arp.query("stats.top-uploaders.limit", 10)
        logger.info(f"执行 '高产上传' 统计，limit={limit}", "群聊语录")
        prolific_uploaders = await QuoteService.get_most_prolific_uploaders(
            group_id_to_query, limit
        )
        if prolific_uploaders:
            result_message = await QuoteService.generate_bar_chart_for_prolific_users(
                group_id_to_query, prolific_uploaders, "语录上传"
            )
        else:
            result_message = f"群组 {group_id_to_query} 暂时没有高产上传用户数据。"

    elif path_to_check == "top-quoted":
        limit = arp.query("stats.top-quoted.limit", 10)
        logger.info(f"执行 '高产被录' 统计，limit={limit}", "群聊语录")
        prolific_quoted = await QuoteService.get_most_quoted_users(
            group_id_to_query, limit
        )
        if prolific_quoted:
            result_message = await QuoteService.generate_bar_chart_for_prolific_users(
                group_id_to_query, prolific_quoted, "被记录语录"
            )
        else:
            result_message = f"群组 {group_id_to_query} 暂时没有高产被记录用户数据。"

    else:
        await MessageUtils.build_message(
            "请指定统计类型：热门、高产上传、高产被录。\n例如：语录统计 热门"
        ).send(target=reply_target, bot=bot)
        return

    try:
        if result_message:
            if isinstance(result_message, str):
                await MessageUtils.build_message(result_message).send(
                    target=reply_target, bot=bot
                )
            elif isinstance(result_message, bytes):
                await MessageUtils.build_message(result_message).send(
                    target=reply_target, bot=bot
                )
        else:
            await MessageUtils.build_message("无法获取统计数据或数据为空。").send(
                target=reply_target, bot=bot
            )
    except Exception as e:
        logger.error(f"语录统计过程中发送消息发生错误: {e}", "群聊语录", e=e)
        await MessageUtils.build_message(f"统计过程中发生错误: {e}").send(
            target=reply_target, bot=bot
        )
