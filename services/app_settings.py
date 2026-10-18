"""系统设置：本地 SQLite 中的使用偏好持久化。

只保存非敏感的使用偏好（回复语言、语气、长度、默认统计范围等）。
API Key 一律不进入数据库，仍由 ``.env`` 或 Streamlit Secrets 管理，
设置页面也只展示“是否已配置”而不回显密钥内容。
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from services.database import connect, now_timestamp


REPLY_LANGUAGES = ("英语", "中文", "中英对照")
REPLY_TONES = ("正式", "友好", "简洁")
REPLY_LENGTHS = ("简短", "标准", "详细")
# 与业务概览页的快捷范围保持一致，便于直接作为默认统计范围。
DEFAULT_TIME_RANGES = ("最近 7 天", "最近 30 天", "最近 90 天", "全部时间")
TABLE_MAX_ROWS_OPTIONS = ("100", "300", "500")
BOOLEAN_OPTIONS = ("true", "false")

# 每个设置项的合法取值；保存与读取都会按此校验，非法值回退到默认值。
SETTING_OPTIONS: dict[str, tuple[str, ...]] = {
    "reply_language": REPLY_LANGUAGES,
    "reply_tone": REPLY_TONES,
    "reply_length": REPLY_LENGTHS,
    "include_chinese_explanation": BOOLEAN_OPTIONS,
    "show_usage_notes": BOOLEAN_OPTIONS,
    "default_time_range": DEFAULT_TIME_RANGES,
    "table_max_rows": TABLE_MAX_ROWS_OPTIONS,
}

DEFAULT_SETTINGS: dict[str, str] = {
    "reply_language": "英语",
    "reply_tone": "正式",
    "reply_length": "标准",
    "include_chinese_explanation": "true",
    "show_usage_notes": "true",
    "default_time_range": "最近 30 天",
    "table_max_rows": "500",
}

# 界面文案，用于设置页表单与偏好说明。
SETTING_LABELS: dict[str, str] = {
    "reply_language": "默认回复语言",
    "reply_tone": "默认商务语气",
    "reply_length": "回复长度偏好",
    "include_chinese_explanation": "生成英文回复时附带中文说明",
    "show_usage_notes": "显示生成结果的使用说明或注意事项",
    "default_time_range": "业务概览默认统计范围",
    "table_max_rows": "询盘明细表最多显示行数",
}


class SettingsError(RuntimeError):
    """设置无法读取、校验或保存。"""


@dataclass(frozen=True)
class AppSettings:
    """已归一化的设置值，页面与提示词直接使用这些字段。"""

    reply_language: str
    reply_tone: str
    reply_length: str
    include_chinese_explanation: bool
    show_usage_notes: bool
    default_time_range: str
    table_max_rows: int

    def as_raw(self) -> dict[str, str]:
        """返回与数据库一致的字符串形式，便于在表单中回填。"""
        return {
            "reply_language": self.reply_language,
            "reply_tone": self.reply_tone,
            "reply_length": self.reply_length,
            "include_chinese_explanation": (
                "true" if self.include_chinese_explanation else "false"
            ),
            "show_usage_notes": "true" if self.show_usage_notes else "false",
            "default_time_range": self.default_time_range,
            "table_max_rows": str(self.table_max_rows),
        }


def initialize_database(db_path: Path | str | None = None) -> None:
    """创建设置表；重复调用安全。"""
    try:
        with closing(connect(db_path)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app_settings (
                        setting_key TEXT PRIMARY KEY,
                        setting_value TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
    except sqlite3.Error as exc:
        raise SettingsError("无法初始化设置数据库。") from exc


def validate_setting(key: str, value: object) -> str:
    """校验单个设置项，返回归一化后的字符串值。"""
    if key not in SETTING_OPTIONS:
        raise SettingsError(f"不支持的设置项：{key}。")
    text = "" if value is None else str(value).strip()
    if key in ("include_chinese_explanation", "show_usage_notes"):
        lowered = text.lower()
        if lowered in ("1", "true", "yes", "on", "是"):
            return "true"
        if lowered in ("0", "false", "no", "off", "否"):
            return "false"
        raise SettingsError(f"{SETTING_LABELS[key]} 只能是开启或关闭。")
    if text not in SETTING_OPTIONS[key]:
        raise SettingsError(
            f"{SETTING_LABELS.get(key, key)}无效，可选值："
            f"{'、'.join(SETTING_OPTIONS[key])}。"
        )
    return text


def _read_stored_values(db_path: Path | str | None) -> dict[str, str]:
    try:
        with closing(connect(db_path)) as connection:
            rows = connection.execute(
                "SELECT setting_key, setting_value FROM app_settings"
            ).fetchall()
    except sqlite3.Error as exc:
        raise SettingsError("读取系统设置失败。") from exc
    return {row["setting_key"]: row["setting_value"] for row in rows}


def load_settings(db_path: Path | str | None = None) -> AppSettings:
    """读取设置并补齐默认值；数据库中的非法值会被安全回退。"""
    initialize_database(db_path)
    stored = _read_stored_values(db_path)
    resolved: dict[str, str] = {}
    for key, default in DEFAULT_SETTINGS.items():
        raw = stored.get(key)
        if raw is None:
            resolved[key] = default
            continue
        try:
            resolved[key] = validate_setting(key, raw)
        except SettingsError:
            resolved[key] = default
    return AppSettings(
        reply_language=resolved["reply_language"],
        reply_tone=resolved["reply_tone"],
        reply_length=resolved["reply_length"],
        include_chinese_explanation=(
            resolved["include_chinese_explanation"] == "true"
        ),
        show_usage_notes=resolved["show_usage_notes"] == "true",
        default_time_range=resolved["default_time_range"],
        table_max_rows=int(resolved["table_max_rows"]),
    )


def save_settings(
    values: Mapping[str, object], db_path: Path | str | None = None
) -> AppSettings:
    """保存部分或全部设置项，返回保存后的完整设置。"""
    resolved = {
        key: validate_setting(key, value) for key, value in values.items()
    }
    if not resolved:
        return load_settings(db_path)
    initialize_database(db_path)
    timestamp = now_timestamp()
    try:
        with closing(connect(db_path)) as connection:
            with connection:
                connection.executemany(
                    """
                    INSERT INTO app_settings (setting_key, setting_value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(setting_key) DO UPDATE SET
                        setting_value = excluded.setting_value,
                        updated_at = excluded.updated_at
                    """,
                    [
                        (key, value, timestamp)
                        for key, value in resolved.items()
                    ],
                )
    except sqlite3.Error as exc:
        raise SettingsError("保存系统设置失败。") from exc
    return load_settings(db_path)


def reset_settings(db_path: Path | str | None = None) -> AppSettings:
    """恢复默认值：清空已保存的设置项，返回默认设置。"""
    initialize_database(db_path)
    try:
        with closing(connect(db_path)) as connection:
            with connection:
                connection.execute("DELETE FROM app_settings")
    except sqlite3.Error as exc:
        raise SettingsError("恢复默认设置失败。") from exc
    return load_settings(db_path)


def describe_settings(settings: AppSettings) -> list[tuple[str, str]]:
    """把设置值转换为可展示的（名称, 文案）列表。"""
    return [
        ("默认回复语言", settings.reply_language),
        ("默认商务语气", settings.reply_tone),
        ("回复长度偏好", settings.reply_length),
        (
            "附带中文说明",
            "开启" if settings.include_chinese_explanation else "关闭",
        ),
        ("显示使用说明", "开启" if settings.show_usage_notes else "关闭"),
        ("默认统计范围", settings.default_time_range),
        ("明细表最多显示行数", str(settings.table_max_rows)),
    ]


def build_reply_preferences_prompt(settings: AppSettings) -> str:
    """把回复偏好转换为提示词片段，供 AI 生成回复时实际生效。

    返回值为中文提示片段；未设置任何有效偏好时仍会给出语言与语气，
    保证设置页面的选择真正影响后续 AI 生成结果。
    """
    if settings.reply_language == "英语":
        language_line = "正文使用英语（英文商务邮件），可以按要求补充中文说明。"
    elif settings.reply_language == "中文":
        language_line = "正文使用中文，必要时保留产品名或贸易术语的英文原文。"
    else:
        language_line = "同时给出英文正文与对应的中文版本，方便直接对照使用。"

    length_line = {
        "简短": "尽量精简，控制在 120 词以内，只保留必要信息。",
        "标准": "长度适中，结构完整但不要堆砌套话。",
        "详细": "可以详细展开，覆盖背景、方案、下一步与需要客户确认的信息。",
    }[settings.reply_length]

    lines = [
        f"- 回复语言：{language_line}",
        f"- 商务语气：{settings.reply_tone}。",
        f"- 回复长度：{length_line}",
    ]
    if settings.include_chinese_explanation:
        lines.append("- 在英文回复之后，附上中文说明，便于业务人员快速核对。")
    else:
        lines.append("- 只输出回复正文，不需要额外的中文说明。")
    if settings.show_usage_notes:
        lines.append(
            "- 结尾补充 2-3 条使用说明或注意事项（例如需要人工核实的内容）。"
        )
    return "\n".join(lines)
