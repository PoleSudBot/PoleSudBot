from __future__ import annotations

import nonebot

nonebot.init()

from zhenxun.plugins.moesekai.b30 import (
    ConstantsTable,
    MusicMeta,
    calculate_best30,
    parse_constants_csv,
    parse_user_music_results,
)


def test_parse_constants_csv_accepts_moebot_columns():
    entries = parse_constants_csv(
        "\ufeffSong,JP Name,Constant,Level,Note Count,Difficulty,Song ID,Notes\n"
        "Tell Your World,Tell Your World,26.4,26,1500,Master,1,\n"
        "Kaiju ni Naritai,怪獣になりたい,38.1,APD 38,2800,Append,671,help\n"
        "YAMINABE!!!!!,ヤミナベ!!!!,37.6,37+,2015,Master,329,\n"
    )

    assert len(entries) == 3
    assert entries[0].music_id == 1
    assert entries[0].difficulty == "master"
    assert entries[0].constant == 26.4
    assert entries[0].note_count == 1500
    assert entries[1].difficulty == "append"
    assert entries[1].level == 38
    assert entries[1].level_label == "38"
    assert entries[2].level == 37
    assert entries[2].level_label == "37"


def test_calculate_best30_keeps_best_result_and_sorts_stably():
    table = ConstantsTable(
        parse_constants_csv(
            "Song,JP Name,Constant,Level,Note Count,Difficulty,Song ID,Notes\n"
            "Song A,,34.0,34,1000,Master,1,\n"
            "Song B,,32.0,32,900,Expert,2,\n"
            "Song C,,31.0,31,800,Master,3,\n"
        )
    )
    results = parse_user_music_results(
        [
            {
                "musicId": 1,
                "musicDifficultyType": "master",
                "playResult": "clear",
                "fullComboFlg": False,
                "fullPerfectFlg": False,
            },
            {
                "musicId": 1,
                "musicDifficultyType": "master",
                "playResult": "full_combo",
                "fullComboFlg": True,
                "fullPerfectFlg": False,
            },
            {
                "musicId": 2,
                "musicDifficultyType": "expert",
                "playResult": "all_perfect",
                "fullComboFlg": True,
                "fullPerfectFlg": True,
            },
            {
                "musicId": 3,
                "musicDifficultyType": "master",
                "playResult": "full_combo",
                "fullComboFlg": True,
                "fullPerfectFlg": False,
            },
        ]
    )

    result = calculate_best30(
        results,
        table,
        lambda music_id, _difficulty, _constant: MusicMeta(
            music_id=music_id,
            title=f"歌曲 {music_id}",
            assetbundle_name=f"jacket_{music_id}",
        ),
    )

    assert [entry.music_id for entry in result.entries] == [1, 2, 3]
    assert [entry.rank for entry in result.entries] == [1, 2, 3]
    assert result.entries[0].user_rating == 33.0
    assert result.entries[0].title == "歌曲 1"
    assert result.entries[1].user_rating == 32.0
    assert result.entries[2].user_rating == 29.5
    assert result.ap_count == 1
    assert result.fc_count == 2
    assert result.candidate_count == 3


def test_calculate_best30_does_not_use_csv_title_as_display_fallback():
    table = ConstantsTable(
        parse_constants_csv(
            "Song,JP Name,Constant,Level,Note Count,Difficulty,Song ID,Notes\n"
            "CSV Song,CSV JP,30.0,30,1000,Master,1,\n"
        )
    )
    results = parse_user_music_results(
        [
            {
                "musicId": 1,
                "musicDifficultyType": "master",
                "playResult": "all_perfect",
                "fullComboFlg": True,
                "fullPerfectFlg": True,
            },
        ]
    )

    result = calculate_best30(results, table)

    assert result.entries[0].title == "歌曲 #1"


def test_calculate_best30_counts_missing_constants():
    table = ConstantsTable(
        parse_constants_csv(
            "Song,JP Name,Constant,Level,Note Count,Difficulty,Song ID,Notes\n"
            "Song A,,30.0,30,1000,Master,1,\n"
        )
    )
    results = parse_user_music_results(
        [
            {
                "musicId": 1,
                "musicDifficultyType": "master",
                "playResult": "all_perfect",
                "fullComboFlg": True,
                "fullPerfectFlg": True,
            },
            {
                "musicId": 9,
                "musicDifficultyType": "master",
                "playResult": "full_combo",
                "fullComboFlg": True,
                "fullPerfectFlg": False,
            },
        ]
    )

    result = calculate_best30(results, table)

    assert [entry.music_id for entry in result.entries] == [1]
    assert result.missing_constants_count == 1
    assert result.total_result_count == 2
