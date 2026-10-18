from __future__ import annotations

import csv
import io
import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from inquiry_parser import detect_country
from services.inquiry_ai import InquiryAIAnalysis


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_ROOT / "data" / "inquiry_history.sqlite3"
FOLLOW_UP_STATUSES = ("待跟进", "跟进中", "已完成")
PRIORITIES = ("高", "中", "低")
# 客户需求字段的固定顺序，同时用于结果展示、缺失信息统计和 CSV 导出。
CUSTOMER_NEED_FIELDS: tuple[tuple[str, str], ...] = (
    ("product", "产品"),
    ("specifications", "规格"),
    ("quantity", "数量"),
    ("target_price", "目标价格"),
    ("delivery_time", "交货时间"),
    ("trade_terms", "贸易条款"),
    ("other_requirements", "其他要求"),
)
# AI 未提取到内容时使用的占位值，统计时按“缺失”处理。
UNKNOWN_NEED_VALUE = "未提供"
# 已知跟进状态（与 FOLLOW_UP_STATUSES 对应），便于统计时明确口径。
PENDING_STATUS = "待跟进"
IN_PROGRESS_STATUS = "跟进中"
COMPLETED_STATUS = "已完成"
# 这些取值都代表“客户未提供该信息”，信息完整度统计时统一按缺失处理。
MISSING_NEED_VALUES = ("", "未提供", "未提及", "未知", "无", "n/a", "none", "null", "-")
# 趋势图支持的统计粒度：按日或按周（周一为每周第一天）。
TREND_GRANULARITIES = ("day", "week")
# “近期新增询盘”的统计窗口：以统计范围的最后一天（或今天）为终点回溯的自然日天数。
RECENT_WINDOW_DAYS = 7


class InquiryHistoryError(RuntimeError):
    """历史询盘记录无法读取或保存。"""


def format_local_timestamp(value: str | None) -> str:
    """把数据库中的 ISO 时间转换为“年-月-日 时:分”展示格式。"""
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return value


def _resolve_date_bound(
    value: date | datetime | str | None,
    field_name: str,
) -> str | None:
    """把日期筛选参数统一为 YYYY-MM-DD 字符串；空值表示不限制。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()).isoformat()
        except ValueError as exc:
            raise InquiryHistoryError(
                f"{field_name}格式无效，请使用 年-月-日。"
            ) from exc
    raise InquiryHistoryError(f"{field_name}格式无效。")


def _build_date_range_clause(
    start_date: date | datetime | str | None,
    end_date: date | datetime | str | None,
) -> tuple[str, list[str]]:
    """构造基于本地日期的筛选条件。

    记录中的 created_at 使用带时区偏移的 ISO 字符串，直接改用 SQLite 的
    date() 会按 UTC 归并从而跨日错位，因此这里统一截取前 10 位作为本地日期。
    """
    start = _resolve_date_bound(start_date, "开始日期")
    end = _resolve_date_bound(end_date, "结束日期")
    if start and end and start > end:
        raise InquiryHistoryError("开始日期不能晚于结束日期。")
    clauses: list[str] = []
    parameters: list[str] = []
    if start:
        clauses.append("substr(created_at, 1, 10) >= ?")
        parameters.append(start)
    if end:
        clauses.append("substr(created_at, 1, 10) <= ?")
        parameters.append(end)
    return " AND ".join(clauses), parameters


def _build_scope_clause(
    start_date: date | datetime | str | None,
    end_date: date | datetime | str | None,
    status: str | None,
) -> tuple[str, list[str]]:
    """把日期范围与跟进状态合并为一组筛选条件。

    指标、趋势、分布、明细表和 CSV 导出都复用同一组条件，保证统计口径一致。
    """
    clause, parameters = _build_date_range_clause(start_date, end_date)
    if status:
        if status not in FOLLOW_UP_STATUSES:
            raise InquiryHistoryError("无效的跟进状态。")
        status_clause = "follow_up_status = ?"
        clause = f"{clause} AND {status_clause}" if clause else status_clause
        parameters.append(status)
    return clause, parameters


def _where_from_clause(clauses: list[str]) -> str:
    return f"WHERE {' AND '.join(clauses)}" if clauses else ""


def _need_value_expression(field: str) -> str:
    """生成从 customer_needs_json 安全提取字段文本的 SQL 表达式。"""
    return (
        "CASE WHEN json_valid(customer_needs_json) "
        f"THEN TRIM(COALESCE(json_extract(customer_needs_json, '$.{field}'), '')) "
        "ELSE '' END"
    )


def _is_missing_need_expression(alias: str) -> str:
    """生成判断某个需求字段是否缺失的 SQL 条件。"""
    placeholders = ", ".join("?" for _ in MISSING_NEED_VALUES)
    return f"({alias} = '' OR LOWER({alias}) IN ({placeholders}))"


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
    # 可选的客户关联：历史记录没有该字段时为 None，仍可正常查看与使用。
    customer_id: str | None = None

    @property
    def country(self) -> str:
        """从原始邮件中识别客户国家；数据库未保存该字段，无法识别时返回“未知”。"""
        return detect_country(self.original_inquiry or "")


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
                        next_follow_up_date TEXT,
                        customer_id TEXT
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
                    "customer_id": (
                        "ALTER TABLE inquiry_history ADD COLUMN customer_id TEXT"
                    ),
                }
                for column, statement in migrations.items():
                    if column not in existing_columns:
                        connection.execute(statement)
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_inquiry_history_customer "
                    "ON inquiry_history (customer_id)"
                )
    except sqlite3.Error as exc:
        raise InquiryHistoryError("无法初始化本地历史记录数据库。") from exc


def save_inquiry_record(
    original_inquiry: str,
    analysis: InquiryAIAnalysis,
    db_path: Path | str | None = None,
    *,
    customer_id: str | None = None,
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
                        follow_up_notes, next_follow_up_date, customer_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        customer_id or None,
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
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    customer_id: str | None = None,
) -> list[InquiryHistoryRecord]:
    """读取历史记录，可按关键词、跟进状态、优先级、日期范围和客户筛选。"""
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
    date_clause, date_parameters = _build_date_range_clause(start_date, end_date)
    if date_clause:
        clauses.append(date_clause)
        parameters.extend(date_parameters)
    if customer_id:
        clauses.append("customer_id = ?")
        parameters.append(customer_id)
    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    try:
        with closing(_connect(db_path)) as connection:
            rows = connection.execute(
                f"""
                SELECT {_RECORD_COLUMNS}
                FROM inquiry_history
                {where_clause}
                ORDER BY created_at DESC, rowid DESC
                """,
                parameters,
            ).fetchall()
    except sqlite3.Error as exc:
        raise InquiryHistoryError("读取历史询盘记录失败。") from exc

    return [_row_to_record(row) for row in rows]


_RECORD_COLUMNS = (
    "record_id, created_at, original_inquiry, translation_zh, "
    "customer_needs_json, intent_level, intent_rationale, risks_json, "
    "reply_draft_en, reply_draft_zh, follow_up_suggestions_json, "
    "follow_up_status, priority, follow_up_notes, next_follow_up_date, "
    "customer_id"
)


def _row_to_record(row: sqlite3.Row) -> InquiryHistoryRecord:
    """把数据库行转换为记录对象；JSON 字段损坏时给出可展示的错误。"""
    try:
        return InquiryHistoryRecord(
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
            follow_up_suggestions=json.loads(row["follow_up_suggestions_json"]),
            follow_up_status=row["follow_up_status"],
            priority=row["priority"],
            follow_up_notes=row["follow_up_notes"],
            next_follow_up_date=row["next_follow_up_date"],
            customer_id=row["customer_id"],
        )
    except (json.JSONDecodeError, TypeError) as exc:
        raise InquiryHistoryError("历史记录数据格式损坏，无法读取。") from exc


def get_inquiry_record(
    record_id: str,
    db_path: Path | str | None = None,
) -> InquiryHistoryRecord | None:
    """按 ID 读取单条询盘记录；不存在时返回 None。"""
    initialize_database(db_path)
    try:
        with closing(_connect(db_path)) as connection:
            row = connection.execute(
                f"SELECT {_RECORD_COLUMNS} FROM inquiry_history "
                "WHERE record_id = ?",
                (record_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise InquiryHistoryError("读取询盘记录失败。") from exc
    return _row_to_record(row) if row else None


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


def count_inquiries_for_customer(
    customer_id: str,
    db_path: Path | str | None = None,
) -> int:
    """统计关联到某客户的询盘数量，用于删除客户前的安全检查。"""
    initialize_database(db_path)
    try:
        with closing(_connect(db_path)) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM inquiry_history WHERE customer_id = ?",
                (customer_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise InquiryHistoryError("统计客户关联询盘失败。") from exc
    return row["total"] or 0


def link_inquiry_to_customer(
    record_id: str,
    customer_id: str | None,
    db_path: Path | str | None = None,
) -> bool:
    """把询盘关联到客户；customer_id 为 None 时解除关联。返回是否更新成功。"""
    initialize_database(db_path)
    try:
        with closing(_connect(db_path)) as connection:
            cursor = connection.execute(
                "UPDATE inquiry_history SET customer_id = ? WHERE record_id = ?",
                (customer_id or None, record_id),
            )
            connection.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as exc:
        raise InquiryHistoryError("关联询盘到客户失败。") from exc


# ---------------------------------------------------------------------------
# 业务数据分析：以下函数只做只读聚合，不改变任何既有记录。
# 说明：数据库没有“已回复/已发送邮件”字段，也没有客户国家字段，
# 因此“回复率”与“按国家统计”无法从现有数据中得到可靠结论。
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InquiryAnalysisSummary:
    """指定筛选范围内的询盘核心指标。"""

    total: int
    pending: int
    in_progress: int
    completed: int
    other_status: int
    high_priority: int
    processed: int
    draft_generated: int
    recent_new: int
    recent_window_days: int
    previous_total: int | None
    completion_rate: float | None
    start_date: str | None
    end_date: str | None
    earliest_date: str | None
    latest_date: str | None


def get_inquiry_analysis_summary(
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    db_path: Path | str | None = None,
    *,
    status: str | None = None,
    today: date | str | None = None,
) -> InquiryAnalysisSummary:
    """单次聚合查询得到核心指标，全部来自真实记录。"""
    initialize_database(db_path)
    clause, scope_parameters = _build_scope_clause(start_date, end_date, status)
    where_clause = _where_from_clause([clause] if clause else [])
    start_bound = _resolve_date_bound(start_date, "开始日期")
    end_bound = _resolve_date_bound(end_date, "结束日期")
    # “近期新增”以统计范围的最后一天为终点；全部时间则以今天为终点。
    reference_bound = end_bound or _resolve_date_bound(today or date.today(), "参考日期")
    window_start = (
        date.fromisoformat(reference_bound) - timedelta(days=RECENT_WINDOW_DAYS - 1)
    ).isoformat()
    try:
        with closing(_connect(db_path)) as connection:
            row = connection.execute(
                f"""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN follow_up_status = ? THEN 1 ELSE 0 END)
                        AS pending,
                    SUM(CASE WHEN follow_up_status = ? THEN 1 ELSE 0 END)
                        AS in_progress,
                    SUM(CASE WHEN follow_up_status = ? THEN 1 ELSE 0 END)
                        AS completed,
                    SUM(CASE WHEN priority = '高' THEN 1 ELSE 0 END)
                        AS high_priority,
                    SUM(CASE WHEN TRIM(COALESCE(reply_draft_en, '')) <> ''
                        THEN 1 ELSE 0 END) AS draft_generated,
                    SUM(CASE WHEN substr(created_at, 1, 10) >= ? THEN 1 ELSE 0 END)
                        AS recent_new,
                    MIN(substr(created_at, 1, 10)) AS earliest_date,
                    MAX(substr(created_at, 1, 10)) AS latest_date
                FROM inquiry_history
                {where_clause}
                """,
                [PENDING_STATUS, IN_PROGRESS_STATUS, COMPLETED_STATUS,
                 window_start, *scope_parameters],
            ).fetchone()
    except sqlite3.Error as exc:
        raise InquiryHistoryError("统计询盘核心指标失败。") from exc

    total = row["total"] or 0
    pending = row["pending"] or 0
    in_progress = row["in_progress"] or 0
    completed = row["completed"] or 0
    return InquiryAnalysisSummary(
        total=total,
        pending=pending,
        in_progress=in_progress,
        completed=completed,
        other_status=max(total - pending - in_progress - completed, 0),
        high_priority=row["high_priority"] or 0,
        processed=in_progress + completed,
        draft_generated=row["draft_generated"] or 0,
        recent_new=row["recent_new"] or 0,
        recent_window_days=RECENT_WINDOW_DAYS,
        previous_total=_count_previous_period(
            start_bound, end_bound, status=status, db_path=db_path
        ),
        completion_rate=(completed / total) if total else None,
        start_date=start_bound,
        end_date=end_bound,
        earliest_date=row["earliest_date"],
        latest_date=row["latest_date"],
    )


def _count_previous_period(
    start_bound: str | None,
    end_bound: str | None,
    *,
    status: str | None,
    db_path: Path | str | None,
) -> int | None:
    """统计与当前筛选范围等长、紧邻其前一段的询盘数，用于环比文案。

    范围不完整（未同时指定起止日期）时返回 None，表示无法比较。
    """
    if not start_bound or not end_bound:
        return None
    length = (date.fromisoformat(end_bound) - date.fromisoformat(start_bound)).days + 1
    previous_end = date.fromisoformat(start_bound) - timedelta(days=1)
    previous_start = previous_end - timedelta(days=length - 1)
    clause, parameters = _build_scope_clause(
        previous_start, previous_end, status
    )
    where_clause = _where_from_clause([clause] if clause else [])
    try:
        with closing(_connect(db_path)) as connection:
            row = connection.execute(
                f"SELECT COUNT(*) AS total FROM inquiry_history {where_clause}",
                parameters,
            ).fetchone()
    except sqlite3.Error as exc:
        raise InquiryHistoryError("统计上一周期询盘数量失败。") from exc
    return row["total"] or 0


def get_inquiry_status_distribution(
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    db_path: Path | str | None = None,
    *,
    status: str | None = None,
) -> list[tuple[str, int]]:
    """按数据库中实际存储的状态值统计数量，未知状态原样返回。"""
    initialize_database(db_path)
    clause, scope_parameters = _build_scope_clause(start_date, end_date, status)
    where_clause = _where_from_clause([clause] if clause else [])
    try:
        with closing(_connect(db_path)) as connection:
            rows = connection.execute(
                f"""
                SELECT COALESCE(follow_up_status, '') AS status,
                       COUNT(*) AS record_count
                FROM inquiry_history
                {where_clause}
                GROUP BY status
                ORDER BY record_count DESC, status ASC
                """,
                scope_parameters,
            ).fetchall()
    except sqlite3.Error as exc:
        raise InquiryHistoryError("统计询盘状态分布失败。") from exc
    return [(row["status"], row["record_count"]) for row in rows]


def get_inquiry_trend(
    granularity: str = "day",
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    db_path: Path | str | None = None,
    *,
    status: str | None = None,
) -> list[tuple[str, int]]:
    """按日或按周统计询盘数量，只保留有记录的区间（升序）。"""
    if granularity not in TREND_GRANULARITIES:
        raise InquiryHistoryError("统计粒度无效。")
    if granularity == "week":
        # 以周一作为每周起点，避免 SQLite date() 的 UTC 归并造成跨日错位。
        period_expression = (
            "date(substr(created_at, 1, 10), 'weekday 0', '-6 days')"
        )
    else:
        period_expression = "substr(created_at, 1, 10)"
    initialize_database(db_path)
    clause, scope_parameters = _build_scope_clause(start_date, end_date, status)
    where_clause = _where_from_clause([clause] if clause else [])
    try:
        with closing(_connect(db_path)) as connection:
            rows = connection.execute(
                f"""
                SELECT {period_expression} AS period,
                       COUNT(*) AS record_count
                FROM inquiry_history
                {where_clause}
                GROUP BY period
                ORDER BY period ASC
                """,
                scope_parameters,
            ).fetchall()
    except sqlite3.Error as exc:
        raise InquiryHistoryError("统计询盘趋势失败。") from exc
    return [(row["period"] or "未知日期", row["record_count"]) for row in rows]


def get_inquiry_product_distribution(
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    limit: int = 8,
    db_path: Path | str | None = None,
    *,
    status: str | None = None,
) -> tuple[list[tuple[str, int]], int]:
    """统计产品需求分布，返回（分布列表, 已识别产品需求的记录数）。"""
    initialize_database(db_path)
    clause, scope_parameters = _build_scope_clause(start_date, end_date, status)
    where_clause = _where_from_clause([clause] if clause else [])
    try:
        with closing(_connect(db_path)) as connection:
            rows = connection.execute(
                f"""
                SELECT product, COUNT(*) AS record_count
                FROM (
                    SELECT {_need_value_expression("product")} AS product
                    FROM inquiry_history
                    {where_clause}
                )
                GROUP BY product
                ORDER BY record_count DESC, product ASC
                """,
                scope_parameters,
            ).fetchall()
    except sqlite3.Error as exc:
        raise InquiryHistoryError("统计产品需求分布失败。") from exc

    missing = {value.lower() for value in MISSING_NEED_VALUES}
    distribution: list[tuple[str, int]] = []
    identified = 0
    for row in rows:
        name = (row["product"] or "").strip()
        if name.lower() in missing:
            continue
        identified += row["record_count"]
        if len(distribution) < limit:
            distribution.append((name, row["record_count"]))
    return distribution, identified


def get_inquiry_country_distribution(
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    limit: int = 8,
    db_path: Path | str | None = None,
    *,
    status: str | None = None,
) -> tuple[list[tuple[str, int]], int, int]:
    """基于邮件正文规则识别国家，返回（分布列表, 已识别数, 范围内总数）。

    数据库没有保存客户国家字段，这里只使用邮件中“based in 某国”这类明确
    表述，识别不到的记录计入“未识别”，不会依据姓名或邮箱进行推断。
    """
    initialize_database(db_path)
    clause, scope_parameters = _build_scope_clause(start_date, end_date, status)
    where_clause = _where_from_clause([clause] if clause else [])
    try:
        with closing(_connect(db_path)) as connection:
            rows = connection.execute(
                f"""
                SELECT original_inquiry
                FROM inquiry_history
                {where_clause}
                """,
                scope_parameters,
            ).fetchall()
    except sqlite3.Error as exc:
        raise InquiryHistoryError("统计客户国家分布失败。") from exc

    counts: dict[str, int] = {}
    identified = 0
    for row in rows:
        country = detect_country(row["original_inquiry"] or "")
        if country == "未知":
            continue
        identified += 1
        counts[country] = counts.get(country, 0) + 1
    distribution = sorted(
        counts.items(), key=lambda item: (-item[1], item[0])
    )[:limit]
    return distribution, identified, len(rows)


def get_inquiry_missing_field_stats(
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    db_path: Path | str | None = None,
    *,
    status: str | None = None,
) -> list[tuple[str, int]]:
    """统计各需求字段被标记为“未提供/未提及”的记录数。"""
    initialize_database(db_path)
    clause, scope_parameters = _build_scope_clause(start_date, end_date, status)
    where_clause = _where_from_clause([clause] if clause else [])
    columns = ", ".join(
        f"{_need_value_expression(field)} AS {field}"
        for field, _ in CUSTOMER_NEED_FIELDS
    )
    sums = ", ".join(
        f"SUM(CASE WHEN {_is_missing_need_expression(field)} THEN 1 ELSE 0 END) "
        f"AS {field}"
        for field, _ in CUSTOMER_NEED_FIELDS
    )
    missing_parameters = [
        value.lower() for value in MISSING_NEED_VALUES
    ] * len(CUSTOMER_NEED_FIELDS)
    try:
        with closing(_connect(db_path)) as connection:
            row = connection.execute(
                f"""
                SELECT {sums}
                FROM (SELECT {columns} FROM inquiry_history {where_clause})
                """,
                [*missing_parameters, *scope_parameters],
            ).fetchone()
    except sqlite3.Error as exc:
        raise InquiryHistoryError("统计信息完整度失败。") from exc
    return [
        (label, row[field] or 0) for field, label in CUSTOMER_NEED_FIELDS
    ]


def get_inquiry_date_bounds(
    db_path: Path | str | None = None,
) -> tuple[str | None, str | None]:
    """返回最早与最晚询盘的本地日期，用于日期选择器的合理默认值。"""
    initialize_database(db_path)
    try:
        with closing(_connect(db_path)) as connection:
            row = connection.execute(
                """
                SELECT MIN(substr(created_at, 1, 10)) AS earliest_date,
                       MAX(substr(created_at, 1, 10)) AS latest_date
                FROM inquiry_history
                """
            ).fetchone()
    except sqlite3.Error as exc:
        raise InquiryHistoryError("读取询盘日期范围失败。") from exc
    return row["earliest_date"], row["latest_date"]


def count_overdue_follow_ups(
    today: date | str | None = None,
    db_path: Path | str | None = None,
) -> int:
    """统计下次跟进日期已过期且尚未完成的询盘数量。"""
    reference = _resolve_date_bound(today, "参考日期") or date.today().isoformat()
    initialize_database(db_path)
    try:
        with closing(_connect(db_path)) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM inquiry_history
                WHERE next_follow_up_date IS NOT NULL
                  AND next_follow_up_date <> ''
                  AND substr(next_follow_up_date, 1, 10) < ?
                  AND follow_up_status <> ?
                """,
                (reference, COMPLETED_STATUS),
            ).fetchone()
    except sqlite3.Error as exc:
        raise InquiryHistoryError("统计逾期跟进失败。") from exc
    return row["total"] or 0


def build_business_insights(
    summary: InquiryAnalysisSummary,
    *,
    status_distribution: list[tuple[str, int]] | None = None,
    product_distribution: list[tuple[str, int]] | None = None,
    missing_field_stats: list[tuple[str, int]] | None = None,
    trend: list[tuple[str, int]] | None = None,
    overdue_follow_ups: int = 0,
) -> list[str]:
    """基于可解释的统计规则生成业务提示，每条都能追溯到具体数量。"""
    if summary.total == 0:
        return [
            "当前筛选范围内没有询盘记录，暂无法生成业务洞察。"
            "可以调整日期范围或先完成一次询盘分析。"
        ]

    insights: list[str] = []
    total = summary.total

    # 环比只使用真实计数，且不把数量变化表述为因果结论。
    if summary.previous_total is not None:
        previous = summary.previous_total
        if previous == 0:
            insights.append(
                f"上一个等长周期内没有询盘记录，本期共 {total} 条。"
            )
        else:
            change = total - previous
            direction = "增加" if change > 0 else "减少" if change < 0 else "持平"
            detail = (
                f"，变化 {abs(change)} 条（{change / previous:+.0%}）"
                if change
                else ""
            )
            insights.append(
                f"与上一个等长周期相比，询盘数量{direction}{detail}"
                f"（本期 {total} 条 / 上期 {previous} 条）。"
            )

    if summary.recent_new:
        insights.append(
            f"统计范围最后 {summary.recent_window_days} 天内新增 "
            f"{summary.recent_new} 条询盘，可优先做首轮回复。"
        )

    if summary.draft_generated < total:
        insights.append(
            f"有 {total - summary.draft_generated} 条询盘尚未生成英文回复草稿，"
            "建议回到“询盘助手”补齐。"
        )

    if summary.pending:
        share = summary.pending / total
        insights.append(
            f"待跟进询盘 {summary.pending} 条，占筛选范围内询盘的 {share:.0%}，"
            "建议优先安排跟进。"
        )
    if summary.in_progress:
        insights.append(
            f"跟进中询盘 {summary.in_progress} 条，"
            "完成后可在“询盘管理”中更新为“已完成”。"
        )
    if summary.completed:
        insights.append(
            f"已完成询盘 {summary.completed} 条，"
            f"占筛选范围内询盘的 {summary.completion_rate:.0%}。"
        )
    if overdue_follow_ups:
        insights.append(
            f"有 {overdue_follow_ups} 条询盘的下次跟进日期已过期且状态不是“已完成”，"
            "建议尽快处理。"
        )
    if summary.other_status:
        insights.append(
            f"有 {summary.other_status} 条记录的状态不在"
            "“待跟进/跟进中/已完成”之内，请在“询盘管理”中核对修正。"
        )

    if missing_field_stats:
        label, count = max(missing_field_stats, key=lambda item: item[1])
        if count:
            insights.append(
                f"信息完整度：共有 {count} 条询盘未提供“{label}”，"
                "建议在首次回复时向客户补齐。"
            )

    if product_distribution:
        product, count = product_distribution[0]
        identified = sum(item[1] for item in product_distribution)
        if identified:
            insights.append(
                f"产品需求最集中在“{product}”，共 {count} 条，"
                f"占已识别产品需求询盘的 {count / identified:.0%}。"
            )

    if trend:
        peak_period, peak_count = max(trend, key=lambda item: item[1])
        average = sum(item[1] for item in trend) / len(trend)
        if peak_count >= 3 and peak_count >= average * 2:
            insights.append(
                f"{peak_period} 收到 {peak_count} 条询盘，"
                f"明显高于统计区间内每个时间单位的平均值 {average:.1f} 条。"
            )

    if status_distribution:
        known = dict(status_distribution)
        if known.get(PENDING_STATUS) and len(status_distribution) == 1:
            insights.append(
                "所有询盘都处于“待跟进”状态，尚未开始跟进记录，"
                "建议先在“询盘管理”中更新处理进度。"
            )

    if not insights:
        insights.append(
            "当前数据量或状态分布较为均匀，暂无可提炼的业务提示；"
            "继续积累询盘记录后可获得更稳定的结论。"
        )
    else:
        insights.append(
            f"以上结论基于当前筛选范围内的 {total} 条记录，"
            "仅描述数据分布，不代表因果关系。"
        )
    if total < 3:
        insights.append(
            f"注意：当前筛选范围内只有 {total} 条询盘，样本量不足，"
            "结论暂不稳定，建议扩大日期范围后再判断。"
        )
    return insights


CSV_EXPORT_HEADERS: tuple[str, ...] = (
    "记录ID",
    "记录时间",
    "跟进状态",
    "业务优先级",
    "下次跟进日期",
    "AI意向判断",
    "客户国家（邮件识别）",
    "产品",
    "规格",
    "数量",
    "目标价格",
    "交货时间",
    "贸易条款",
    "其他要求",
    "跟进备注",
    "原始英文询盘",
    "中文翻译",
    "英文回复草稿",
    "风险与待确认事项",
    "后续跟进建议",
)


def export_inquiry_records_csv(
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    db_path: Path | str | None = None,
    *,
    status: str | None = None,
    priority: str | None = None,
) -> str:
    """导出筛选范围内的询盘记录为 CSV 文本（含 BOM，便于 Excel 打开）。

    只包含客户询盘与分析结果等业务字段，不含任何密钥或内部配置。
    """
    return build_inquiry_records_csv(
        list_inquiry_records(
            db_path,
            status=status,
            priority=priority,
            start_date=start_date,
            end_date=end_date,
        )
    )


def _safe_csv_value(value: object) -> str:
    """Prevent spreadsheet formula execution in fields originating from business data."""
    text = "" if value is None else str(value)
    formula_candidate = text.lstrip(" \t\r\n\u00a0")
    if text.startswith(("\t", "\r", "\n")) or formula_candidate.startswith(
        ("=", "+", "-", "@")
    ):
        return "'" + text
    return text


def build_inquiry_records_csv(records: list[InquiryHistoryRecord]) -> str:
    """把已读取的记录转换为 CSV 文本，避免页面重复查询数据库。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_EXPORT_HEADERS)
    for record in records:
        needs = record.customer_needs
        country = record.country
        writer.writerow(
            _safe_csv_value(value)
            for value in [
                record.record_id,
                format_local_timestamp(record.created_at),
                record.follow_up_status,
                record.priority,
                record.next_follow_up_date or "",
                record.intent_level,
                "" if country == "未知" else country,
                *[
                    str(needs.get(field, "")).strip()
                    for field, _ in CUSTOMER_NEED_FIELDS
                ],
                record.follow_up_notes,
                record.original_inquiry,
                record.translation_zh,
                record.reply_draft_en,
                "；".join(record.risks_and_questions),
                "；".join(record.follow_up_suggestions),
            ]
        )
    return "\ufeff" + buffer.getvalue()
