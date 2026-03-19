from __future__ import annotations

from dataclasses import dataclass

import nonebot

nonebot.init()

from zhenxun.plugins.moesekai.command_parser import parse_command


@dataclass
class FakeSegment:
    type: str
    data: dict


class FakeEvent:
    def __init__(self, text: str, segments: list[FakeSegment] | None = None):
        self._text = text
        self._segments = segments or []

    def get_plaintext(self) -> str:
        return self._text

    def get_message(self) -> list[FakeSegment]:
        return self._segments


def test_parse_personal_archive_with_prefix():
    parsed = parse_command(FakeEvent("cn个人档案"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "personal_archive"
    assert parsed.server == "cn"


def test_parse_query_archive_with_at_target():
    parsed = parse_command(
        FakeEvent("查询档案", [FakeSegment("at", {"qq": "123456"})])  # type: ignore[arg-type]
    )
    assert parsed is not None
    assert parsed.action == "query_archive"
    assert parsed.target_user_id == "123456"
    assert parsed.game_id is None


def test_parse_bind_with_server_after_command():
    parsed = parse_command(FakeEvent("绑定 jp 1234567890123"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "bind"
    assert parsed.server == "jp"
    assert parsed.game_id == "1234567890123"


def test_parse_activity_deck_with_optional_flags():
    parsed = parse_command(
        FakeEvent("活动组卡 195 --music 226 --difficulty hard --live-type multi")  # type: ignore[arg-type]
    )
    assert parsed is not None
    assert parsed.action == "activity_deck"
    assert parsed.event_id == 195
    assert parsed.music_id == 226
    assert parsed.difficulty == "hard"
    assert parsed.live_type == "multi"


def test_parse_activity_deck_with_position_args_and_aliases():
    parsed = parse_command(FakeEvent("活动组卡 195 226 hd cheerful"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "activity_deck"
    assert parsed.event_id == 195
    assert parsed.music_id == 226
    assert parsed.difficulty == "hard"
    assert parsed.live_type == "cheerful"


def test_parse_activity_deck_duplicate_music_arg_errors():
    parsed = parse_command(
        FakeEvent("活动组卡 195 226 --music 227")  # type: ignore[arg-type]
    )
    assert parsed is not None
    assert parsed.action == "activity_deck"
    assert parsed.error == "歌曲ID参数重复"


def test_parse_update_with_prefix_server():
    parsed = parse_command(FakeEvent("cnpjsk update"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "update"
    assert parsed.server == "cn"
    assert parsed.all_servers is False


def test_parse_update_all():
    parsed = parse_command(FakeEvent("pjsk update all"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "update"
    assert parsed.server is None
    assert parsed.all_servers is True


def test_parse_ycx_with_event_id():
    parsed = parse_command(FakeEvent("cnycx 166"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "ycx"
    assert parsed.server == "cn"
    assert parsed.event_id == 166


def test_parse_prediction_with_event_id():
    parsed = parse_command(FakeEvent("jpsk预测 178"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "prediction"
    assert parsed.server == "jp"
    assert parsed.event_id == 178


def test_parse_ycx_extra_arg_errors():
    parsed = parse_command(FakeEvent("ycx 166 extra"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "ycx"
    assert parsed.error == "ycx只支持一个可选活动ID"


def test_parse_admin_query_binding_qq_with_at():
    parsed = parse_command(
        FakeEvent("pjsk 查询绑定 qq", [FakeSegment("at", {"qq": "654321"})])  # type: ignore[arg-type]
    )
    assert parsed is not None
    assert parsed.action == "admin_query_binding"
    assert parsed.admin_target_type == "qq"
    assert parsed.admin_value == "654321"
