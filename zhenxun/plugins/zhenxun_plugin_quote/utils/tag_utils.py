from typing import Any

from arclet.alconna import Arparma

from ..services.quote_service import QuoteService


def _extend_parts(collected_parts: list[Any], raw_parts: Any) -> None:
    if raw_parts is None:
        return
    if isinstance(raw_parts, (list, tuple)):
        collected_parts.extend(raw_parts)
        return
    collected_parts.append(raw_parts)


def collect_tag_parts(arp: Arparma, arg_name: str = "parts") -> list[Any]:
    collected_parts: list[Any] = []

    _extend_parts(collected_parts, arp.all_matched_args.get(arg_name))
    extra_args = arp.main_args.get("$extra", [])
    _extend_parts(collected_parts, extra_args)

    return collected_parts


def extract_manual_tags(arp: Arparma, arg_name: str = "parts") -> list[str]:
    return QuoteService.parse_tag_segments(collect_tag_parts(arp, arg_name))
