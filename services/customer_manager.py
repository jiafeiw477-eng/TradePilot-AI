"""客户管理：本地 SQLite 中的客户增删改查、校验与关联询盘检查。

删除策略：客户一旦存在关联询盘，默认不做级联删除，而是转为“归档”，
避免历史询盘和跟进记录被连带清空。归档客户默认不出现在列表中，可恢复。
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import services.inquiry_history as inquiry_history
from services.database import connect, now_timestamp


CUSTOMER_LEVELS = ("普通", "潜在重点", "重点客户")
CUSTOMER_STATUSES = ("潜在客户", "跟进中", "已成交", "暂停跟进")
DEFAULT_LEVEL = "普通"
DEFAULT_STATUS = "潜在客户"
# 常用客户来源，允许留空；页面以候选下拉方式呈现，避免自由文本造成统计歧义。
CUSTOMER_SOURCES = (
    "展会",
    "B2B 平台",
    "官网询盘",
    "邮件开发",
    "客户推荐",
    "老客户复购",
    "其他",
)
MAX_NAME_LENGTH = 200
MAX_SHORT_FIELD_LENGTH = 200
MAX_TEXT_LENGTH = 4000
# 基础邮箱格式校验：只要包含“非空白@非空白.非空白”即视为格式正确。
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CustomerError(RuntimeError):
    """客户数据无法读取、校验或保存。"""


@dataclass(frozen=True)
class Customer:
    customer_id: str
    company_name: str
    contact_name: str
    email: str
    phone: str
    country: str
    website: str
    source: str
    level: str
    status: str
    notes: str
    last_contact_date: str | None
    is_archived: int
    created_at: str
    updated_at: str

    @property
    def display_label(self) -> str:
        """下拉选择时使用的展示名：公司名 + 可选联系人。"""
        if self.contact_name:
            return f"{self.company_name}（{self.contact_name}）"
        return self.company_name

    @property
    def archived(self) -> bool:
        return bool(self.is_archived)


def initialize_database(db_path: Path | str | None = None) -> None:
    """创建客户表与索引；重复调用安全。"""
    try:
        with closing(connect(db_path)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS customers (
                        customer_id TEXT PRIMARY KEY,
                        company_name TEXT NOT NULL,
                        contact_name TEXT NOT NULL DEFAULT '',
                        email TEXT NOT NULL DEFAULT '',
                        phone TEXT NOT NULL DEFAULT '',
                        country TEXT NOT NULL DEFAULT '',
                        website TEXT NOT NULL DEFAULT '',
                        source TEXT NOT NULL DEFAULT '',
                        level TEXT NOT NULL DEFAULT '普通',
                        status TEXT NOT NULL DEFAULT '潜在客户',
                        notes TEXT NOT NULL DEFAULT '',
                        last_contact_date TEXT,
                        is_archived INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                existing_columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(customers)")
                }
                migrations = {
                    "is_archived": (
                        "ALTER TABLE customers ADD COLUMN "
                        "is_archived INTEGER NOT NULL DEFAULT 0"
                    ),
                    "last_contact_date": (
                        "ALTER TABLE customers ADD COLUMN last_contact_date TEXT"
                    ),
                }
                for column, statement in migrations.items():
                    if column not in existing_columns:
                        connection.execute(statement)
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_customers_company "
                    "ON customers (company_name COLLATE NOCASE)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_customers_country "
                    "ON customers (country)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_customers_status "
                    "ON customers (status)"
                )
    except sqlite3.Error as exc:
        raise CustomerError("无法初始化客户管理数据库。") from exc


def _clean_text(value: object, *, field: str, max_length: int, required: bool) -> str:
    text = "" if value is None else str(value).strip()
    if required and not text:
        raise CustomerError(f"{field}不能为空。")
    if len(text) > max_length:
        raise CustomerError(f"{field}过长，请控制在 {max_length} 个字符以内。")
    return text


def validate_email(value: object) -> str:
    """校验邮箱格式；允许留空，因为部分客户只有电话或展会名片。"""
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    if not EMAIL_PATTERN.match(text):
        raise CustomerError("邮箱格式无效，请填写类似 name@example.com 的地址。")
    return text


def _validate_choice(value: str, options: tuple[str, ...], field: str) -> str:
    if value not in options:
        raise CustomerError(f"{field}无效，请选择：{'、'.join(options)}。")
    return value


def _validate_last_contact_date(value: object) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()).isoformat()
        except ValueError as exc:
            raise CustomerError("最近联系日期格式无效，请使用 年-月-日。") from exc
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())[:10]
    raise CustomerError("最近联系日期格式无效。")


def _validate_customer_input(
    *,
    company_name: object,
    contact_name: object,
    email: object,
    phone: object,
    country: object,
    website: object,
    source: object,
    level: object,
    status: object,
    notes: object,
    last_contact_date: object,
) -> dict[str, object]:
    if isinstance(last_contact_date, str) and last_contact_date.strip() == "":
        last_contact_date = None
    return {
        "company_name": _clean_text(
            company_name,
            field="公司名称",
            max_length=MAX_NAME_LENGTH,
            required=True,
        ),
        "contact_name": _clean_text(
            contact_name,
            field="联系人",
            max_length=MAX_SHORT_FIELD_LENGTH,
            required=False,
        ),
        "email": validate_email(email),
        "phone": _clean_text(
            phone, field="电话", max_length=MAX_SHORT_FIELD_LENGTH, required=False
        ),
        "country": _clean_text(
            country,
            field="国家或地区",
            max_length=MAX_SHORT_FIELD_LENGTH,
            required=False,
        ),
        "website": _clean_text(
            website,
            field="公司网站",
            max_length=MAX_SHORT_FIELD_LENGTH,
            required=False,
        ),
        "source": _clean_text(
            source, field="客户来源", max_length=MAX_SHORT_FIELD_LENGTH, required=False
        ),
        "level": _validate_choice(
            _clean_text(level, field="客户等级", max_length=20, required=False)
            or DEFAULT_LEVEL,
            CUSTOMER_LEVELS,
            "客户等级",
        ),
        "status": _validate_choice(
            _clean_text(status, field="客户状态", max_length=20, required=False)
            or DEFAULT_STATUS,
            CUSTOMER_STATUSES,
            "客户状态",
        ),
        "notes": _clean_text(
            notes, field="客户备注", max_length=MAX_TEXT_LENGTH, required=False
        ),
        "last_contact_date": _validate_last_contact_date(last_contact_date),
    }


def _find_duplicate(
    company_name: str,
    email: str,
    *,
    db_path: Path | str | None,
    exclude_customer_id: str | None = None,
) -> str | None:
    """查找公司名称与邮箱都相同的客户，返回其 customer_id。"""
    clause = (
        "TRIM(company_name) = ? COLLATE NOCASE "
        "AND TRIM(COALESCE(email, '')) = ? COLLATE NOCASE"
    )
    parameters: list[str] = [company_name, email]
    if exclude_customer_id:
        clause += " AND customer_id <> ?"
        parameters.append(exclude_customer_id)
    with closing(connect(db_path)) as connection:
        row = connection.execute(
            f"SELECT customer_id FROM customers WHERE {clause}", parameters
        ).fetchone()
    return row["customer_id"] if row else None


def create_customer(
    company_name: object,
    *,
    contact_name: object = "",
    email: object = "",
    phone: object = "",
    country: object = "",
    website: object = "",
    source: object = "",
    level: object = DEFAULT_LEVEL,
    status: object = DEFAULT_STATUS,
    notes: object = "",
    last_contact_date: object = None,
    db_path: Path | str | None = None,
) -> str:
    """新增客户并返回客户 ID；公司名称与邮箱完全重复时给出提醒。"""
    fields = _validate_customer_input(
        company_name=company_name,
        contact_name=contact_name,
        email=email,
        phone=phone,
        country=country,
        website=website,
        source=source,
        level=level,
        status=status,
        notes=notes,
        last_contact_date=last_contact_date,
    )
    initialize_database(db_path)
    try:
        duplicate = _find_duplicate(
            str(fields["company_name"]), str(fields["email"]), db_path=db_path
        )
    except sqlite3.Error as exc:
        raise CustomerError("检查重复客户失败。") from exc
    if duplicate:
        raise CustomerError(
            "已存在公司名称与邮箱完全相同的客户记录，"
            "请直接编辑该客户，避免重复建档。"
        )

    customer_id = str(uuid.uuid4())
    timestamp = now_timestamp()
    try:
        with closing(connect(db_path)) as connection:
            connection.execute(
                """
                INSERT INTO customers (
                    customer_id, company_name, contact_name, email, phone,
                    country, website, source, level, status, notes,
                    last_contact_date, is_archived, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    customer_id,
                    fields["company_name"],
                    fields["contact_name"],
                    fields["email"],
                    fields["phone"],
                    fields["country"],
                    fields["website"],
                    fields["source"],
                    fields["level"],
                    fields["status"],
                    fields["notes"],
                    fields["last_contact_date"],
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()
    except sqlite3.Error as exc:
        raise CustomerError("保存客户失败，请检查数据库文件状态。") from exc
    return customer_id


def update_customer(
    customer_id: str,
    *,
    company_name: object,
    contact_name: object = "",
    email: object = "",
    phone: object = "",
    country: object = "",
    website: object = "",
    source: object = "",
    level: object = DEFAULT_LEVEL,
    status: object = DEFAULT_STATUS,
    notes: object = "",
    last_contact_date: object = None,
    db_path: Path | str | None = None,
) -> bool:
    """更新客户资料；返回是否确实更新了一条记录。"""
    fields = _validate_customer_input(
        company_name=company_name,
        contact_name=contact_name,
        email=email,
        phone=phone,
        country=country,
        website=website,
        source=source,
        level=level,
        status=status,
        notes=notes,
        last_contact_date=last_contact_date,
    )
    initialize_database(db_path)
    try:
        duplicate = _find_duplicate(
            str(fields["company_name"]),
            str(fields["email"]),
            db_path=db_path,
            exclude_customer_id=customer_id,
        )
    except sqlite3.Error as exc:
        raise CustomerError("检查重复客户失败。") from exc
    if duplicate:
        raise CustomerError(
            "已存在公司名称与邮箱完全相同的客户记录，请核对后修改。"
        )

    try:
        with closing(connect(db_path)) as connection:
            cursor = connection.execute(
                """
                UPDATE customers
                SET company_name = ?, contact_name = ?, email = ?, phone = ?,
                    country = ?, website = ?, source = ?, level = ?, status = ?,
                    notes = ?, last_contact_date = ?, updated_at = ?
                WHERE customer_id = ?
                """,
                (
                    fields["company_name"],
                    fields["contact_name"],
                    fields["email"],
                    fields["phone"],
                    fields["country"],
                    fields["website"],
                    fields["source"],
                    fields["level"],
                    fields["status"],
                    fields["notes"],
                    fields["last_contact_date"],
                    now_timestamp(),
                    customer_id,
                ),
            )
            connection.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as exc:
        raise CustomerError("更新客户失败，请检查数据库文件状态。") from exc


_CUSTOMER_COLUMNS = (
    "customer_id, company_name, contact_name, email, phone, country, website, "
    "source, level, status, notes, last_contact_date, is_archived, created_at, "
    "updated_at"
)


def _row_to_customer(row: sqlite3.Row) -> Customer:
    return Customer(
        customer_id=row["customer_id"],
        company_name=row["company_name"],
        contact_name=row["contact_name"],
        email=row["email"],
        phone=row["phone"],
        country=row["country"],
        website=row["website"],
        source=row["source"],
        level=row["level"],
        status=row["status"],
        notes=row["notes"],
        last_contact_date=row["last_contact_date"],
        is_archived=row["is_archived"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def list_customers(
    db_path: Path | str | None = None,
    *,
    keyword: str = "",
    country: str | None = None,
    level: str | None = None,
    status: str | None = None,
    include_archived: bool = False,
) -> list[Customer]:
    """按关键词、国家、等级和状态筛选客户，按更新时间从新到旧返回。"""
    if level is not None and level not in CUSTOMER_LEVELS:
        raise CustomerError("无效的客户等级。")
    if status is not None and status not in CUSTOMER_STATUSES:
        raise CustomerError("无效的客户状态。")
    initialize_database(db_path)
    clauses: list[str] = []
    parameters: list[str] = []
    if not include_archived:
        clauses.append("is_archived = 0")
    if keyword.strip():
        pattern = f"%{keyword.strip()}%"
        clauses.append(
            "(company_name LIKE ? COLLATE NOCASE OR contact_name LIKE ? COLLATE NOCASE "
            "OR email LIKE ? COLLATE NOCASE OR phone LIKE ? COLLATE NOCASE)"
        )
        parameters.extend([pattern] * 4)
    if country:
        clauses.append("country = ?")
        parameters.append(country)
    if level is not None:
        clauses.append("level = ?")
        parameters.append(level)
    if status is not None:
        clauses.append("status = ?")
        parameters.append(status)
    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    try:
        with closing(connect(db_path)) as connection:
            rows = connection.execute(
                f"""
                SELECT {_CUSTOMER_COLUMNS}
                FROM customers
                {where_clause}
                ORDER BY updated_at DESC, rowid DESC
                """,
                parameters,
            ).fetchall()
    except sqlite3.Error as exc:
        raise CustomerError("读取客户列表失败。") from exc
    return [_row_to_customer(row) for row in rows]


def get_customer(
    customer_id: str, db_path: Path | str | None = None
) -> Customer | None:
    """按 ID 读取单个客户；不存在时返回 None。"""
    initialize_database(db_path)
    try:
        with closing(connect(db_path)) as connection:
            row = connection.execute(
                f"SELECT {_CUSTOMER_COLUMNS} FROM customers WHERE customer_id = ?",
                (customer_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise CustomerError("读取客户详情失败。") from exc
    return _row_to_customer(row) if row else None


def archive_customer(customer_id: str, db_path: Path | str | None = None) -> bool:
    """归档客户：保留资料与关联询盘，仅从默认列表中隐藏。"""
    initialize_database(db_path)
    try:
        with closing(connect(db_path)) as connection:
            cursor = connection.execute(
                "UPDATE customers SET is_archived = 1, updated_at = ? "
                "WHERE customer_id = ?",
                (now_timestamp(), customer_id),
            )
            connection.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as exc:
        raise CustomerError("归档客户失败。") from exc


def restore_customer(customer_id: str, db_path: Path | str | None = None) -> bool:
    """把归档客户恢复到默认列表。"""
    initialize_database(db_path)
    try:
        with closing(connect(db_path)) as connection:
            cursor = connection.execute(
                "UPDATE customers SET is_archived = 0, updated_at = ? "
                "WHERE customer_id = ?",
                (now_timestamp(), customer_id),
            )
            connection.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as exc:
        raise CustomerError("恢复客户失败。") from exc


def delete_customer(
    customer_id: str, db_path: Path | str | None = None
) -> str:
    """删除客户并返回处理结果。

    - ``"archived"``：客户存在关联询盘，已改为归档，历史询盘不受影响。
    - ``"deleted"``：没有任何关联询盘，已从数据库删除。
    - ``"missing"``：客户不存在。
    """
    customer = get_customer(customer_id, db_path)
    if customer is None:
        return "missing"
    try:
        linked = inquiry_history.count_inquiries_for_customer(customer_id, db_path)
    except inquiry_history.InquiryHistoryError as exc:
        raise CustomerError(str(exc)) from exc
    if linked:
        return "archived" if archive_customer(customer_id, db_path) else "missing"
    try:
        with closing(connect(db_path)) as connection:
            cursor = connection.execute(
                "DELETE FROM customers WHERE customer_id = ?", (customer_id,)
            )
            connection.commit()
            return "deleted" if cursor.rowcount > 0 else "missing"
    except sqlite3.Error as exc:
        raise CustomerError("删除客户失败。") from exc


def list_countries(db_path: Path | str | None = None) -> list[str]:
    """返回已有客户国家/地区，用于筛选下拉框，只包含真实存在的值。"""
    initialize_database(db_path)
    try:
        with closing(connect(db_path)) as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT TRIM(country) AS country
                FROM customers
                WHERE TRIM(country) <> ''
                ORDER BY country COLLATE NOCASE ASC
                """
            ).fetchall()
    except sqlite3.Error as exc:
        raise CustomerError("读取客户国家列表失败。") from exc
    return [row["country"] for row in rows]


def get_customer_stats(db_path: Path | str | None = None) -> dict[str, int]:
    """统计客户总数、重点客户与成交情况，全部来自真实记录。"""
    initialize_database(db_path)
    try:
        with closing(connect(db_path)) as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN is_archived = 1 THEN 1 ELSE 0 END) AS archived,
                    SUM(CASE WHEN level = '重点客户' THEN 1 ELSE 0 END) AS key_accounts,
                    SUM(CASE WHEN status = '已成交' THEN 1 ELSE 0 END) AS won
                FROM customers
                """
            ).fetchone()
    except sqlite3.Error as exc:
        raise CustomerError("统计客户数据失败。") from exc
    return {
        "total": row["total"] or 0,
        "archived": row["archived"] or 0,
        "key_accounts": row["key_accounts"] or 0,
        "won": row["won"] or 0,
    }


def find_customers_for_query(
    text: str,
    *,
    limit: int = 3,
    db_path: Path | str | None = None,
) -> list[Customer]:
    """在真实客户资料中查找与用户文本相关的客户。

    按公司名称与联系人是否直接出现在文本中匹配，找不到时返回空列表，
    由调用方明确告知用户“没有找到匹配客户”，不编造客户历史。
    """
    normalized = (text or "").strip().lower()
    if not normalized:
        return []
    matches: list[Customer] = []
    for customer in list_customers(db_path, include_archived=True):
        candidates = (customer.company_name, customer.contact_name)
        if any(
            candidate and candidate.lower() in normalized for candidate in candidates
        ):
            matches.append(customer)
            if len(matches) >= limit:
                break
    return matches
