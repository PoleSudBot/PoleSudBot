from __future__ import annotations

from dataclasses import dataclass

import nonebot

nonebot.init()

from zhenxun.plugins.moesekai.command_parser import parse_command
from zhenxun.plugins.moesekai.deck import DeckCommandRequest, parse_deck_command_request


@dataclass
class FakeSegment:
    type: str
    data: dict


class FakeEvent:
    def __init__(
        self,
        text: str,
        segments: list[FakeSegment] | None = None,
        *,
        self_id: str = "999999",
    ):
        self._text = text
        self._segments = segments or []
        self.self_id = self_id

    def get_plaintext(self) -> str:
        return self._text

    def get_message(self) -> list[FakeSegment]:
        return self._segments


def test_parse_personal_archive_with_prefix():
    parsed = parse_command(FakeEvent("cn个人档案"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "query_archive"
    assert parsed.server == "cn"
    assert parsed.game_id is None
    assert parsed.target_user_id is None


def test_parse_query_archive_with_at_target():
    parsed = parse_command(
        FakeEvent("查询档案", [FakeSegment("at", {"qq": "123456"})])  # type: ignore[arg-type]
    )
    assert parsed is not None
    assert parsed.action == "query_archive"
    assert parsed.target_user_id == "123456"
    assert parsed.game_id is None


def test_parse_personal_archive_with_at_target():
    parsed = parse_command(
        FakeEvent("个人档案", [FakeSegment("at", {"qq": "123456"})])  # type: ignore[arg-type]
    )
    assert parsed is not None
    assert parsed.action == "query_archive"
    assert parsed.target_user_id == "123456"
    assert parsed.game_id is None


def test_parse_personal_archive_with_uid_target_without_space():
    parsed = parse_command(FakeEvent("个人档案1234567890123"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "query_archive"
    assert parsed.server is None
    assert parsed.game_id == "1234567890123"


def test_parse_personal_archive_with_uid_target_ignores_bot_at():
    parsed = parse_command(
        FakeEvent(
            "个人档案1234567890123",
            [FakeSegment("at", {"qq": "114514"})],
            self_id="114514",
        )  # type: ignore[arg-type]
    )
    assert parsed is not None
    assert parsed.action == "query_archive"
    assert parsed.game_id == "1234567890123"
    assert parsed.target_user_id is None


def test_parse_personal_archive_compact_non_digit_suffix_is_ignored():
    assert parse_command(FakeEvent("个人档案abc")) is None  # type: ignore[arg-type]


def test_parse_personal_archive_compact_short_numeric_suffix_is_ignored():
    assert parse_command(FakeEvent("个人档案123")) is None  # type: ignore[arg-type]


def test_parse_bind_with_server_after_command():
    parsed = parse_command(FakeEvent("绑定 jp 1234567890123"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "bind"
    assert parsed.server == "jp"
    assert parsed.game_id == "1234567890123"


def test_parse_bind_without_space_before_game_id():
    parsed = parse_command(FakeEvent("绑定1234567890123"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "bind"
    assert parsed.server is None
    assert parsed.game_id == "1234567890123"


def test_parse_activity_deck_with_optional_flags():
    parsed = parse_command(
        FakeEvent("活动组卡 195 --music 226 --difficulty hard --live-type multi")  # type: ignore[arg-type]
    )
    assert parsed is not None
    assert parsed.action == "deck"
    assert isinstance(parsed.deck_request, DeckCommandRequest)
    assert parsed.deck_request.mode == "event"
    assert parsed.deck_request.event_id == 195
    assert parsed.deck_request.music_query == "226"
    assert parsed.deck_request.difficulty == "hard"
    assert parsed.deck_request.live_type == "multi"


def test_parse_activity_deck_with_position_args_and_aliases():
    parsed = parse_command(FakeEvent("活动组卡 195 226 hd cheerful"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "deck"
    assert isinstance(parsed.deck_request, DeckCommandRequest)
    assert parsed.deck_request.mode == "event"
    assert parsed.deck_request.event_id == 195
    assert parsed.deck_request.music_query == "226"
    assert parsed.deck_request.difficulty == "hard"
    assert parsed.deck_request.live_type == "cheerful"


def test_parse_activity_deck_duplicate_music_arg_errors():
    parsed = parse_command(
        FakeEvent("活动组卡 195 226 --music 227")  # type: ignore[arg-type]
    )
    assert parsed is not None
    assert parsed.action == "deck"
    assert parsed.error == "歌曲ID参数重复"


def test_parse_activity_deck_without_space_before_event_id():
    parsed = parse_command(FakeEvent("活动组卡195 --music 226"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "deck"
    assert isinstance(parsed.deck_request, DeckCommandRequest)
    assert parsed.deck_request.mode == "event"
    assert parsed.deck_request.event_id == 195
    assert parsed.deck_request.music_query == "226"


def test_parse_activity_deck_with_music_alias_text():
    parsed = parse_command(FakeEvent("活动组卡 tell your world master"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "deck"
    assert isinstance(parsed.deck_request, DeckCommandRequest)
    assert parsed.deck_request.mode == "event"
    assert parsed.deck_request.event_id is None
    assert parsed.deck_request.music_query == "tell your world"
    assert parsed.deck_request.difficulty == "master"


def test_parse_custom_deck_command_is_hidden():
    parsed = parse_command(FakeEvent("组卡 绿 vbs"))  # type: ignore[arg-type]
    assert parsed is None


def test_parse_custom_deck_request_unit_bonus():
    request, error = parse_deck_command_request(
        raw_text="组卡 绿 vbs",
        mode="custom",
        server=None,
        rest="绿 vbs",
        at_targets=[],
    )
    assert error is None
    assert isinstance(request, DeckCommandRequest)
    assert request.mode == "custom"
    assert request.custom_bonus is not None
    assert request.custom_bonus.kind == "unit"
    assert request.custom_bonus.attr == "pure"
    assert request.custom_bonus.unit == "vivid_bad_squad"
    assert request.music_query is None


def test_parse_custom_deck_request_unit_bonus_reverse_order():
    request, error = parse_deck_command_request(
        raw_text="组卡 vbs 绿",
        mode="custom",
        server=None,
        rest="vbs 绿",
        at_targets=[],
    )
    assert error is None
    assert isinstance(request, DeckCommandRequest)
    assert request.custom_bonus is not None
    assert request.custom_bonus.kind == "unit"
    assert request.custom_bonus.attr == "pure"
    assert request.custom_bonus.unit == "vivid_bad_squad"


def test_parse_custom_deck_request_mixed_bonus():
    request, error = parse_deck_command_request(
        raw_text="组卡 %miku rin @绿",
        mode="custom",
        server=None,
        rest="%miku rin @绿",
        at_targets=[],
    )
    assert error is None
    assert isinstance(request, DeckCommandRequest)
    assert request.mode == "custom"
    assert request.custom_bonus is not None
    assert request.custom_bonus.kind == "mixed"
    assert request.custom_bonus.attr == "pure"
    assert [item.query for item in request.custom_bonus.characters] == [
        "miku",
        "rin",
    ]


def test_parse_custom_deck_request_with_vs_support_prefix():
    request, error = parse_deck_command_request(
        raw_text="组卡 %lnmiku vbsrin @绿",
        mode="custom",
        server=None,
        rest="%lnmiku vbsrin @绿",
        at_targets=[],
    )
    assert error is None
    assert isinstance(request, DeckCommandRequest)
    assert request.custom_bonus is not None
    assert [item.support_unit for item in request.custom_bonus.characters] == [
        "leo_need",
        "vivid_bad_squad",
    ]


def test_parse_mysekai_deck():
    parsed = parse_command(FakeEvent("烤森组卡 201"))  # type: ignore[arg-type]
    assert parsed is not None
    assert isinstance(parsed.deck_request, DeckCommandRequest)
    assert parsed.deck_request.mode == "mysekai"
    assert parsed.deck_request.event_id == 201


def test_parse_strongest_deck_defaults_to_power():
    parsed = parse_command(FakeEvent("最强组卡"))  # type: ignore[arg-type]
    assert parsed is not None
    assert isinstance(parsed.deck_request, DeckCommandRequest)
    assert parsed.deck_request.mode == "strongest"
    assert parsed.deck_request.strongest_target is None
    assert parsed.deck_request.music_query is None


def test_parse_strongest_deck_skill_target_and_music():
    parsed = parse_command(FakeEvent("最强组卡 Tell Your World 实效"))  # type: ignore[arg-type]
    assert parsed is not None
    assert isinstance(parsed.deck_request, DeckCommandRequest)
    assert parsed.deck_request.mode == "strongest"
    assert parsed.deck_request.strongest_target == "skill"
    assert parsed.deck_request.music_query == "tell your world"


def test_parse_challenge_deck():
    parsed = parse_command(FakeEvent("挑战组卡 初音未来 Tell Your World master"))  # type: ignore[arg-type]
    assert parsed is not None
    assert isinstance(parsed.deck_request, DeckCommandRequest)
    assert parsed.deck_request.mode == "challenge"
    assert parsed.deck_request.free_text_query == "初音未来 tell your world"
    assert parsed.deck_request.difficulty == "master"


def test_parse_challenge_deck_requires_character():
    parsed = parse_command(FakeEvent("挑战组卡"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "deck"
    assert parsed.error == "用法: 挑战组卡 <角色> [歌曲] [难度]"


def test_parse_custom_deck_request_rejects_missing_bonus_pair():
    request, error = parse_deck_command_request(
        raw_text="组卡 绿",
        mode="custom",
        server=None,
        rest="绿",
        at_targets=[],
    )
    assert isinstance(request, DeckCommandRequest)
    assert error == "箱活组卡需要同时提供颜色和团体，例如：组卡 绿 vbs"


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


def test_parse_ycx_without_space_before_event_id():
    parsed = parse_command(FakeEvent("ycx166"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "ycx"
    assert parsed.server is None
    assert parsed.event_id == 166


def test_parse_prediction_with_event_id():
    parsed = parse_command(FakeEvent("jpsk预测 178"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "prediction"
    assert parsed.server == "jp"
    assert parsed.event_id == 178


def test_parse_prediction_without_space_before_event_id():
    parsed = parse_command(FakeEvent("sk预测178"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "prediction"
    assert parsed.server is None
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


def test_parse_story_command():
    parsed = parse_command(FakeEvent("活动剧情 199"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "story"
    assert parsed.event_id == 199
    assert parsed.force_refresh is False


def test_parse_story_without_space_before_event_id():
    parsed = parse_command(FakeEvent("活动剧情199"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "story"
    assert parsed.event_id == 199
    assert parsed.force_refresh is False


def test_parse_story_force_refresh_suffix():
    parsed = parse_command(FakeEvent("活动剧情 199 强制刷新"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "story"
    assert parsed.event_id == 199
    assert parsed.force_refresh is True


def test_parse_story_force_refresh_prefix():
    parsed = parse_command(FakeEvent("活动剧情 强制刷新 199"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "story"
    assert parsed.event_id == 199
    assert parsed.force_refresh is True


def test_parse_story_rejects_duplicate_force_refresh():
    parsed = parse_command(FakeEvent("活动剧情 强制刷新 199 强制刷新"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "story"
    assert parsed.error == "用法: 活动剧情 <活动ID> [强制刷新]"


def test_parse_random_manga_command():
    parsed = parse_command(FakeEvent("随机四格"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "random_manga"


def test_parse_specific_manga_command():
    parsed = parse_command(FakeEvent("四格 351"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "manga_by_id"
    assert parsed.manga_id == 351


def test_parse_specific_manga_without_space():
    parsed = parse_command(FakeEvent("四格351"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "manga_by_id"
    assert parsed.manga_id == 351


def test_parse_specific_manga_requires_single_numeric_arg():
    assert parse_command(FakeEvent("四格")) is None  # type: ignore[arg-type]
    assert parse_command(FakeEvent("四格 miku")) is None  # type: ignore[arg-type]
    assert parse_command(FakeEvent("四格 12 34")) is None  # type: ignore[arg-type]


def test_parse_character_command_is_archived():
    assert parse_command(FakeEvent("查角色 初音未来")) is None  # type: ignore[arg-type]


def test_parse_multiplier_command():
    parsed = parse_command(FakeEvent("倍率计算 150 130 120 115 100"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "multiplier"
    assert parsed.multiplier_values == [150, 130, 120, 115, 100]


def test_parse_multiplier_short_name_command():
    parsed = parse_command(FakeEvent("倍率 150 130 120 115 100"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "multiplier"
    assert parsed.multiplier_values == [150, 130, 120, 115, 100]


def test_parse_multiplier_requires_five_numeric_args():
    parsed = parse_command(FakeEvent("倍率计算 150 130 120"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "multiplier"
    assert parsed.error == "用法: 倍率计算 <a> <b> <c> <d> <e>"

    parsed = parse_command(FakeEvent("倍率计算 150 130 120 115 abc"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "multiplier"
    assert parsed.error == "用法: 倍率计算 <a> <b> <c> <d> <e>"


def test_parse_live_toggle_with_server():
    parsed = parse_command(FakeEvent("live提醒 开启 jp"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "live_toggle"
    assert parsed.admin_subaction == "enable"
    assert parsed.server == "jp"


def test_parse_live_toggle_with_attached_subaction():
    parsed = parse_command(FakeEvent("live提醒开启 jp"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "live_toggle"
    assert parsed.admin_subaction == "enable"
    assert parsed.server == "jp"


def test_parse_live_toggle_requires_space_after_subaction():
    parsed = parse_command(FakeEvent("live提醒开启jp"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "live_toggle"
    assert parsed.error == "用法: live提醒 <开启|关闭|状态> [区服]"


def test_parse_live_subscribe_with_prefix_server():
    parsed = parse_command(FakeEvent("jp订阅live提醒"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "live_subscribe"
    assert parsed.admin_subaction == "subscribe"
    assert parsed.server == "jp"


def test_parse_live_subscribe_with_attached_server():
    parsed = parse_command(FakeEvent("订阅live提醒jp"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "live_subscribe"
    assert parsed.admin_subaction == "subscribe"
    assert parsed.server == "jp"


def test_parse_new_card_toggle_supports_new_and_legacy_name():
    parsed = parse_command(FakeEvent("新卡上线提醒 开启 jp"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "new_card_toggle"
    assert parsed.admin_subaction == "enable"
    assert parsed.server == "jp"

    legacy = parse_command(FakeEvent("新卡提醒 状态 jp"))  # type: ignore[arg-type]
    assert legacy is not None
    assert legacy.action == "new_card_toggle"
    assert legacy.admin_subaction == "status"
    assert legacy.server == "jp"


def test_parse_alias_add_global():
    parsed = parse_command(FakeEvent("角色别名 添加 全局 初音未来 miku"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "alias"
    assert parsed.admin_target_type == "character"
    assert parsed.admin_subaction == "add"
    assert parsed.global_scope is True
    assert parsed.query_text == "初音未来"
    assert parsed.alias == "miku"


def test_parse_character_alias_defaults_to_query():
    parsed = parse_command(FakeEvent("角色别名 初音未来"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "alias"
    assert parsed.admin_target_type == "character"
    assert parsed.admin_subaction == "query"
    assert parsed.query_text == "初音未来"


def test_parse_character_alias_without_space():
    parsed = parse_command(FakeEvent("角色别名初音未来"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "alias"
    assert parsed.admin_target_type == "character"
    assert parsed.admin_subaction == "query"
    assert parsed.query_text == "初音未来"


def test_parse_music_alias_defaults_to_query():
    parsed = parse_command(FakeEvent("歌曲别名 Tell Your World"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "alias"
    assert parsed.admin_target_type == "music"
    assert parsed.admin_subaction == "query"
    assert parsed.query_text == "tell your world"


def test_parse_music_alias_without_space():
    parsed = parse_command(FakeEvent("歌曲别名277"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "alias"
    assert parsed.admin_target_type == "music"
    assert parsed.admin_subaction == "query"
    assert parsed.query_text == "277"


def test_parse_query_archive_without_space_before_game_id():
    parsed = parse_command(FakeEvent("查询档案1234567890123"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "query_archive"
    assert parsed.server is None
    assert parsed.game_id == "1234567890123"


def test_parse_query_archive_compact_short_numeric_suffix_is_ignored():
    assert parse_command(FakeEvent("查询档案123")) is None  # type: ignore[arg-type]


def test_parse_query_archive_without_target_defaults_to_self_query():
    parsed = parse_command(FakeEvent("查询档案"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "query_archive"
    assert parsed.server is None
    assert parsed.game_id is None
    assert parsed.target_user_id is None


def test_parse_archive_query_rejects_mixed_uid_and_at_target():
    parsed = parse_command(
        FakeEvent(
            "个人档案 1234567890123",
            [FakeSegment("at", {"qq": "123456"})],
        )  # type: ignore[arg-type]
    )
    assert parsed is not None
    assert parsed.action == "query_archive"
    assert parsed.error == "档案查询不能同时指定游戏ID和 @用户"


def test_parse_archive_query_rejects_multiple_at_targets():
    parsed = parse_command(
        FakeEvent(
            "查询档案",
            [
                FakeSegment("at", {"qq": "123456"}),
                FakeSegment("at", {"qq": "654321"}),
            ],
        )  # type: ignore[arg-type]
    )
    assert parsed is not None
    assert parsed.action == "query_archive"
    assert parsed.error == "档案查询最多只能指定一个 @ 用户"


def test_parse_default_server_without_space_before_server():
    parsed = parse_command(FakeEvent("默认区服jp"))  # type: ignore[arg-type]
    assert parsed is not None
    assert parsed.action == "default_server"
    assert parsed.server == "jp"


def test_parse_admin_update_still_requires_space_after_pjsk():
    assert parse_command(FakeEvent("pjskupdate")) is None  # type: ignore[arg-type]
