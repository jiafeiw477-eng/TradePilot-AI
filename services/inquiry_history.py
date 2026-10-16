from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from services.inquiry_ai import InquiryAIAnalysis


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_ROOT / "data" / "inquiry_history.sqlite3"
FOLLOW_UP_STATUSES = ("待跟进", "跟进中", "已完成")
PRIORITIES = ("高", "中", "低")


class InquiryHistoryError(RuntimeError):
    """历史询盘记录无法读取或保存。"""


@dataclass(frozen=True)
class InquiryHistoryRecord:
    record_id: str
    created_at: str
    original_inquiry: str
    translation_zh: str
    customer_needs: dict[str, str]
    intent_level: str
    intent_rationale: str
    risks_and_questions: list[str]
    reply_draft_en: str
    reply_draft_zh: str
    follow_up_suggestions: list[str]
    follow_up_status: str
    priority: str
    follow_up_notes: str
    next_follow_up_date: str | None


@dataclass(frozen=True)
class InquiryHistoryStats:
    total: int
    pending: int
    in_progress: int
    completed: int
    high_priority: int


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else DATABASE_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection
    except (OSError, sqlite3.Error) as exc:
        raise InquiryHistoryError("无法打开本地历史记录数据库。") from exc


def initialize_database(db_path: Path | str | None = None) -> None:
    """创建本地历史记录数据库及表；重复调用安全。"""
    try:
        with closing(_connect(db_path)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS inquiry_history (
                        record_id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        original_inquiry TEXT NOT NULL,
                        translation_zh TEXT NOT NULL,
                        customer_needs_json TEXT NOT NULL,
                        intent_level TEXT NOT NULL,
                        intent_rationale TEXT NOT NULL,
                        risks_json TEXT NOT NULL,
                        reply_draft_en TEXT NOT NULL,
                        reply_draft_zh TEXT NOT NULL,
                        follow_up_suggestions_json TEXT NOT NULL,
                        follow_up_status TEXT NOT NULL DEFAULT '待跟进',
                        priority TEXT NOT NULL DEFAULT '中',
                        follow_up_notes TEXT NOT NULL DEFAULT '',
                        next_follow_up_date TEXT
                    )
                    """
                )
                existing_columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(inquiry_history)"
                    )
                }
                migrations = {
                    "follow_up_status": (
                        "ALTER TABLE inquiry_history ADD COLUMN "
                        "follow_up_status TEXT NOT NULL DEFAULT '待跟进'"
                    ),
                    "priority": (
                        "ALTER TABLE inquiry_history ADD COLUMN "
                        "priority TEXT NOT NULL DEFAULT '中'"
                    ),
                    "follow_up_notes": (
                        "ALTER TABLE inquiry_history ADD COLUMN "
                        "follow_up_notes TEXT NOT NULL DEFAULT ''"
                    ),
                    "next_follow_up_date": (
                        "ALTER TABLE inquiry_history ADD COLUMN "
                        "next_follow_up_date TEXT"
                    ),
                }
                for column, statement in migrations.items():
                    if column not in existing_columns:
                        connection.execute(statement)
    except sqlite3.Error as exc:
        raise InquiryHistoryError("无法初始化本地历史记录数据库。") from exc


def save_inquiry_record(
    original_inquiry: str,
    analysis: InquiryAIAnalysis,
    db_path: Path | str | None = None,
) -> str:
    """保存一次成功的结构化分析，并返回新记录 ID。"""
    initialize_database(db_path)
    record_id = str(uuid.uuid4())
    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        with closing(_connect(db_path)) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO inquiry_history (
                        record_id, created_at, original_inquiry, translation_zh,
                        customer_needs_json, intent_level, intent_rationale,
                        risks_json, reply_draft_en, reply_draft_zh,
                        follow_up_suggestions_json, follow_up_status, priority,
                        follow_up_notes, next_follow_up_date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record_id,
                        created_at,
                        original_inquiry,
                        analysis.translation_zh,
                        json.dumps(analysis.customer_needs, ensure_ascii=False),
                        analysis.intent_level,
                        analysis.intent_rationale,
                        json.dumps(analysis.risks_and_questions, ensure_ascii=False),
                        analysis.reply_draft_en,
                        analysis.reply_draft_zh,
                        json.dumps(
                            analysis.follow_up_suggestions,
                            ensure_ascii=False,
                        ),
                        "待跟进",
                        "中",
                        "",
                        None,
                    ),
                )
    except (sqlite3.Error, OSError) as exc:
        raise InquiryHistoryError("保存历史询盘记录失败，请检查数据库文件状态。") from exc
    return record_id


def list_inquiry_records(
    db_path: Path | str | None = None,
    *,
    keyword: str = "",
    status: str | None = None,
    priority: str | None = None,
) -> list[InquiryHistoryRecord]:
    """读取历史记录，可按关键词、跟进状态和优先级筛选。"""
    initialize_database(db_path)
    clauses: list[str] = []
    parameters: list[str] = []
    if keyword.strip():
        clauses.append(
            "("
            "original_inquiry LIKE ? COLLATE NOCASE OR "
            "translation_zh LIKE ? COLLATE NOCASE OR "
            "customer_needs_json LIKE ? COLLATE NOCASE OR "
            "follow_up_notes LIKE ? COLLATE NOCASE"
            ")"
        )
        pattern = f"%{keyword.strip()}%"
        parameters.extend([pattern] * 4)
    if status is not None:
        if status not in FOLLOW_UP_STATUSES:
            raise InquiryHistoryError("无效的跟进状态。")
        clauses.append("follow_up_status = ?")
        parameters.append(status)
    if priority is not None:
        if priority not in PRIORITIES:
            raise InquiryHistoryError("无效的询盘优先级。")
        clauses.append("priority = ?")
        parameters.append(priority)
    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    try:
        with closing(_connect(db_path)) as connection:
            rows = connection.execute(
                f"""
                SELECT record_id, created_at, original_inquiry, translation_zh,
                       customer_needs_json, intent_level, intent_rationale,
                       risks_json, reply_draft_en, reply_draft_zh,
                       follow_up_suggestions_json, follow_up_status, priority,
                       follow_up_notes, next_follow_up_date
                FROM inquiry_history
                {where_clause}
                ORDER BY created_at DESC, rowid DESC
                """,
                parameters,
            ).fetchall()
    except sqlite3.Error as exc:
        raise InquiryHistoryError("读取历史询盘记录失败。") from exc

    try:
        return [
            InquiryHistoryRecord(
                record_id=row["record_id"],
                created_at=row["created_at"],
                original_inquiry=row["original_inquiry"],
                translation_zh=row["translation_zh"],
                customer_needs=json.loads(row["customer_needs_json"]),
                intent_level=row["intent_level"],
                intent_rationale=row["intent_rationale"],
                risks_and_questions=json.loads(row["risks_json"]),
                reply_draft_en=row["reply_draft_en"],
                reply_draft_zh=row["reply_draft_zh"],
                follow_up_suggestions=json.loads(
                    row["follow_up_suggestions_json"]
                ),
                follow_up_status=row["follow_up_status"],
                priority=row["priority"],
                follow_up_notes=row["follow_up_notes"],
                next_follow_up_date=row["next_follow_up_date"],
            )
            for row in rows
        ]
    except (json.JSONDecodeError, TypeError) as exc:
        raise InquiryHistoryError("历史记录数据格式损坏，无法读取。") from exc


def get_inquiry_history_stats(
    db_path: Path | str | None = None,
) -> InquiryHistoryStats:
    """统计全部询盘的总量、状态和高优先级数量。"""
    initialize_database(db_path)
    try:
        with closing(_connect(db_path)) as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN follow_up_status = '待跟进' THEN 1 ELSE 0 END)
                        AS pending,
                    SUM(CASE WHEN follow_up_status = '跟进中' THEN 1 ELSE 0 END)
                        AS in_progress,
                    SUM(CASE WHEN follow_up_status = '已完成' THEN 1 ELSE 0 END)
                        AS completed,
                    SUM(CASE WHEN priority = '高' THEN 1 ELSE 0 END)
                        AS high_priority
                FROM inquiry_history
                """
            ).fetchone()
    except sqlite3.Error as exc:
        raise InquiryHistoryError("统计询盘管理数据失败。") from exc
    return InquiryHistoryStats(
        total=row["total"] or 0,
        pending=row["pending"] or 0,
        in_progress=row["in_progress"] or 0,
        completed=row["completed"] or 0,
        high_priority=row["high_priority"] or 0,
    )


def update_inquiry_follow_up(
    record_id: str,
    *,
    status: str,
    priority: str,
    notes: str,
    next_follow_up_date: str | None,
    db_path: Path | str | None = None,
) -> bool:
    """保存询盘管理字段；校验状态、优先级和 ISO 日期。"""
    if status not in FOLLOW_UP_STATUSES:
        raise InquiryHistoryError("无效的跟进状态。")
    if priority not in PRIORITIES:
        raise InquiryHistoryError("无效的询盘优先级。")
    if next_follow_up_date:
        try:
            date.fromisoformat(next_follow_up_date)
        except ValueError as exc:
            raise InquiryHistoryError("下次跟进日期格式无效。") from exc
    else:
        next_follow_up_date = None

    initialize_database(db_path)
    try:
        with closing(_connect(db_path)) as connection:
            cursor = connection.execute(
                """
                UPDATE inquiry_history
                SET follow_up_status = ?, priority = ?, follow_up_notes = ?,
                    next_follow_up_date = ?
                WHERE record_id = ?
                """,
                (status, priority, notes, next_follow_up_date, record_id),
            )
            connection.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as exc:
        raise InquiryHistoryError("保存询盘跟进信息失败。") from exc


def delete_inquiry_record(
    record_id: str,
    db_path: Path | str | None = None,
) -> bool:
    """删除指定记录；返回是否确实删除了一条记录。"""
    initialize_database(db_path)
    try:
        with closing(_connect(db_path)) as connection:
            cursor = connection.execute(
                "DELETE FROM inquiry_history WHERE record_id = ?",
                (record_id,),
            )
            connection.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as exc:
        raise InquiryHistoryError("删除历史询盘记录失败。") from exc
