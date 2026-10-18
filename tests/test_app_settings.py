from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from services.app_settings import (
    DEFAULT_SETTINGS,
    SETTING_OPTIONS,
    SettingsError,
    build_reply_preferences_prompt,
    describe_settings,
    initialize_database,
    load_settings,
    reset_settings,
    save_settings,
    validate_setting,
)


def test_defaults_are_used_without_stored_values(tmp_path) -> None:
    db_path = tmp_path / "data" / "settings.sqlite3"

    settings = load_settings(db_path)

    assert settings.reply_language == "英语"
    assert settings.reply_tone == "正式"
    assert settings.reply_length == "标准"
    assert settings.include_chinese_explanation is True
    assert settings.show_usage_notes is True
    assert settings.default_time_range == "最近 30 天"
    assert settings.table_max_rows == 500


def test_database_is_created_idempotently(tmp_path) -> None:
    db_path = tmp_path / "settings.sqlite3"

    initialize_database(db_path)
    initialize_database(db_path)

    assert db_path.is_file()


def test_save_then_load_round_trips_values(tmp_path) -> None:
    db_path = tmp_path / "settings.sqlite3"

    saved = save_settings(
        {
            "reply_language": "中英对照",
            "reply_tone": "简洁",
            "reply_length": "详细",
            "include_chinese_explanation": "false",
            "default_time_range": "最近 90 天",
            "table_max_rows": "300",
        },
        db_path,
    )

    assert saved.reply_language == "中英对照"
    assert saved.reply_tone == "简洁"
    assert saved.reply_length == "详细"
    assert saved.include_chinese_explanation is False
    assert saved.default_time_range == "最近 90 天"
    assert saved.table_max_rows == 300

    reloaded = load_settings(db_path)
    assert reloaded == saved
    assert reloaded.as_raw()["table_max_rows"] == "300"


def test_partial_save_keeps_other_values(tmp_path) -> None:
    db_path = tmp_path / "settings.sqlite3"
    save_settings({"reply_tone": "友好"}, db_path)

    settings = save_settings({"reply_length": "简短"}, db_path)

    assert settings.reply_tone == "友好"
    assert settings.reply_length == "简短"
    assert settings.reply_language == DEFAULT_SETTINGS["reply_language"]


def test_invalid_values_are_rejected(tmp_path) -> None:
    db_path = tmp_path / "settings.sqlite3"

    with pytest.raises(SettingsError, match="不支持的设置项"):
        save_settings({"unknown_key": "x"}, db_path)
    with pytest.raises(SettingsError, match="默认回复语言无效"):
        save_settings({"reply_language": "法语"}, db_path)
    with pytest.raises(SettingsError, match="只能是开启或关闭"):
        save_settings({"show_usage_notes": "也许"}, db_path)
    with pytest.raises(SettingsError, match="业务概览默认统计范围无效"):
        save_settings({"default_time_range": "最近 1 天"}, db_path)


def test_stored_invalid_value_falls_back_to_default(tmp_path) -> None:
    db_path = tmp_path / "settings.sqlite3"
    initialize_database(db_path)
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute(
            "INSERT INTO app_settings (setting_key, setting_value, updated_at) "
            "VALUES ('reply_tone', '咆哮', '2026-01-01T00:00:00+08:00')"
        )
        connection.commit()

    settings = load_settings(db_path)

    assert settings.reply_tone == DEFAULT_SETTINGS["reply_tone"]


def test_reset_settings_restores_defaults(tmp_path) -> None:
    db_path = tmp_path / "settings.sqlite3"
    save_settings({"reply_language": "中文", "table_max_rows": "100"}, db_path)

    restored = reset_settings(db_path)

    assert restored.reply_language == DEFAULT_SETTINGS["reply_language"]
    assert restored.table_max_rows == int(DEFAULT_SETTINGS["table_max_rows"])


def test_boolean_aliases_are_normalized() -> None:
    assert validate_setting("show_usage_notes", "1") == "true"
    assert validate_setting("show_usage_notes", "on") == "true"
    assert validate_setting("show_usage_notes", "是") == "true"
    assert validate_setting("show_usage_notes", "否") == "false"
    assert validate_setting("show_usage_notes", "false") == "false"


def test_api_key_is_never_a_supported_setting(tmp_path) -> None:
    """设置表只能保存非敏感偏好，密钥类字段必须完全不存在。"""
    db_path = tmp_path / "settings.sqlite3"
    save_settings({"reply_language": "中文"}, db_path)

    assert not any("key" in name.lower() or "secret" in name.lower() for name in SETTING_OPTIONS)
    assert not any(
        "api" in name.lower() or "token" in name.lower()
        for name in DEFAULT_SETTINGS
    )
    with closing(sqlite3.connect(db_path)) as connection:
        stored_keys = {
            row[0] for row in connection.execute("SELECT setting_key FROM app_settings")
        }
    assert stored_keys == {"reply_language"}


def test_describe_settings_and_reply_preferences_prompt(tmp_path) -> None:
    db_path = tmp_path / "settings.sqlite3"
    settings = save_settings(
        {
            "reply_language": "英语",
            "reply_tone": "正式",
            "reply_length": "简短",
            "include_chinese_explanation": "true",
            "show_usage_notes": "false",
        },
        db_path,
    )

    described = dict(describe_settings(settings))
    assert described["默认回复语言"] == "英语"
    assert described["附带中文说明"] == "开启"
    assert described["显示使用说明"] == "关闭"

    prompt = build_reply_preferences_prompt(settings)
    assert "英语" in prompt
    assert "正式" in prompt
    assert "120 词以内" in prompt
    assert "中文说明" in prompt
    # 关闭使用说明后不应再要求模型输出使用说明段落。
    assert "使用说明或注意事项" not in prompt
