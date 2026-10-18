"""共享的 SQLite 访问层：统一数据库路径与连接创建。

数据库路径以 ``services.inquiry_history.DATABASE_PATH`` 为唯一来源：
调用时才读取该属性，因此测试只要 monkeypatch 这一个属性，
询盘、产品、客户和设置就会全部落到同一个隔离的测试库中。
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

import services.inquiry_history as inquiry_history


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DatabaseError(RuntimeError):
    """本地数据库无法访问或操作失败。"""


def resolve_database_path(db_path: Path | str | None = None) -> Path:
    """返回实际使用的数据库文件路径；未显式指定时使用项目默认库。"""
    if db_path is not None:
        return Path(db_path)
    return Path(inquiry_history.DATABASE_PATH)


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """创建 SQLite 连接；调用方负责关闭。"""
    path = resolve_database_path(db_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection
    except (OSError, sqlite3.Error) as exc:
        raise DatabaseError("无法打开本地数据库文件。") from exc


def now_timestamp() -> str:
    """返回带本地时区偏移的 ISO 时间字符串，与询盘记录格式保持一致。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


# 允许统计记录数的业务表白名单，避免把表名当作自由输入拼接进 SQL。
KNOWN_TABLES = ("inquiry_history", "products", "customers", "app_settings")


def table_exists(table: str, db_path: Path | str | None = None) -> bool:
    """检查数据表是否存在，用于系统信息里的真实连接检查。"""
    with closing(connect(db_path)) as connection:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
    return row is not None


def count_table_rows(table: str, db_path: Path | str | None = None) -> int:
    """统计业务表内的记录数；表名必须在白名单内。"""
    if table not in KNOWN_TABLES:
        raise DatabaseError("不支持统计该数据表。")
    with closing(connect(db_path)) as connection:
        row = connection.execute(
            f"SELECT COUNT(*) AS total FROM {table}"  # noqa: S608 - 表名来自白名单
        ).fetchone()
    return int(row["total"] or 0)
