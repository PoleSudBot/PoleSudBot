from __future__ import annotations

from datetime import datetime
import json
from zoneinfo import ZoneInfo

import pytest

from zhenxun.plugins.mc_server.utils import (
    MC_TIMEZONE,
    build_tellraw_command,
    classify_tellraw_response,
    downsample_points,
    format_server_address,
    is_bind_flow_done,
    is_bind_flow_exit,
    is_bind_flow_skip,
    normalize_datetime,
    now_local,
    parse_rcon_list_online_count,
    parse_server_address,
    parse_time_range,
    resolve_latest_log_path,
)


def test_parse_server_address_defaults_java_port():
    parsed = parse_server_address("mc.example.com")

    assert parsed.host == "mc.example.com"
    assert parsed.port == 25565


def test_parse_server_address_accepts_port_and_bracket_ipv6():
    assert parse_server_address("mc.example.com:25566").port == 25566

    parsed = parse_server_address("[::1]:25567")

    assert parsed.host == "::1"
    assert parsed.port == 25567
    assert parsed.display == "[::1]:25567"


def test_parse_server_address_rejects_invalid_port_and_unbracketed_ipv6():
    with pytest.raises(ValueError, match="端口"):
        parse_server_address("mc.example.com:abc")

    with pytest.raises(ValueError, match="IPv6"):
        parse_server_address("::1:25565")


def test_format_server_address_brackets_ipv6_hosts():
    assert format_server_address("mc.example.com", 25565) == "mc.example.com:25565"
    assert format_server_address("::1", 25565) == "[::1]:25565"


def test_parse_time_range_uses_season_by_default():
    season_start = datetime(2026, 5, 1, 12, 0, 0)

    result = parse_time_range("", season_start)

    assert result.label == "本周目"
    assert result.start == season_start.replace(tzinfo=MC_TIMEZONE)
    assert result.end >= result.start


def test_parse_time_range_accepts_single_day_and_range():
    one_day = parse_time_range("2026-05-15")
    ranged = parse_time_range("2026-05-01..2026-05-03")

    assert one_day.label == "2026-05-15"
    assert one_day.start == datetime(2026, 5, 15, 0, 0, 0, tzinfo=MC_TIMEZONE)
    assert ranged.label == "2026-05-01..2026-05-03"
    assert ranged.start == datetime(2026, 5, 1, 0, 0, 0, tzinfo=MC_TIMEZONE)


def test_parse_time_range_rejects_reversed_range():
    with pytest.raises(ValueError, match="结束日期"):
        parse_time_range("2026-05-03..2026-05-01")


def test_build_tellraw_command_escapes_json_payload():
    command = build_tellraw_command(
        '群"友',
        "hello\n/world",
        "[{sender}] {message}",
    )
    prefix, payload = command.split(" ", 2)[0:2], command.split(" ", 2)[2]

    assert prefix == ["tellraw", "@a"]
    assert json.loads(payload) == {"text": '[群"友] hello\n/world'}


def test_datetime_helpers_normalize_to_mc_timezone():
    aware_utc = datetime(2026, 5, 16, 12, 0, 0, tzinfo=ZoneInfo("UTC"))

    assert now_local().tzinfo is not None
    assert normalize_datetime(datetime(2026, 5, 16, 20, 0, 0)) == datetime(
        2026, 5, 16, 20, 0, 0, tzinfo=MC_TIMEZONE
    )
    assert normalize_datetime(aware_utc) == datetime(
        2026, 5, 16, 20, 0, 0, tzinfo=MC_TIMEZONE
    )


def test_parse_rcon_list_online_count():
    response = "There are 2 of a max of 20 players online: Steve, Alex"

    assert parse_rcon_list_online_count(response) == 2
    assert parse_rcon_list_online_count("Players online: Steve") is None


def test_classify_tellraw_response_silences_success_and_no_recipient():
    assert classify_tellraw_response("") is None
    assert classify_tellraw_response("No player was found") is None
    assert classify_tellraw_response("找不到玩家") is None


def test_classify_tellraw_response_returns_clear_failures():
    response = 'Unknown command. Type "/help" for help.'

    assert classify_tellraw_response(response) == response


def test_bind_flow_control_words():
    assert is_bind_flow_exit(" q ")
    assert is_bind_flow_exit("QUIT")
    assert is_bind_flow_exit("退出")
    assert is_bind_flow_exit("取消")
    assert not is_bind_flow_exit("done")

    assert is_bind_flow_skip("skip")
    assert is_bind_flow_skip("跳过")
    assert not is_bind_flow_skip("q")

    assert is_bind_flow_done("done")
    assert is_bind_flow_done("完成")
    assert not is_bind_flow_done("skip")


def test_resolve_latest_log_path_accepts_file_and_directory(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    latest_log = log_dir / "latest.log"
    latest_log.write_text("[Server thread/INFO]: Done\n", encoding="utf-8")

    assert resolve_latest_log_path(str(latest_log)) == latest_log
    assert resolve_latest_log_path(str(log_dir)) == latest_log


def test_resolve_latest_log_path_rejects_missing_path(tmp_path):
    assert resolve_latest_log_path(str(tmp_path / "missing.log")) is None


def test_downsample_points_preserves_first_and_last_items():
    points = list(range(10))

    result = downsample_points(points, 4)

    assert result[0] == 0
    assert result[-1] == 9
    assert len(result) == 4
