"""产品目录：本地 SQLite 中的产品增删改查、校验与目录检索。

所有金额与数量字段都以文本形式接收（页面上是可留空的输入框），
在校验层统一转换为数值后写入数据库，避免无效值进入业务数据。
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from services.database import connect, now_timestamp


# 产品状态：草稿表示尚未对外报价，停售仍保留历史资料。
PRODUCT_STATUSES = ("在售", "停售", "草稿")
# 价格可使用的货币单位，避免出现自由文本导致的统计歧义。
CURRENCIES = ("USD", "EUR", "CNY", "GBP", "JPY", "AUD")
DEFAULT_CURRENCY = "USD"
DEFAULT_STATUS = "草稿"
# 单字段长度上限，防止误粘贴超长内容进入数据库。
MAX_NAME_LENGTH = 200
MAX_SKU_LENGTH = 80
MAX_SHORT_FIELD_LENGTH = 200
MAX_TEXT_LENGTH = 4000


class ProductError(RuntimeError):
    """产品数据无法读取、校验或保存。"""


@dataclass(frozen=True)
class Product:
    product_id: str
    name: str
    sku: str
    category: str
    description: str
    price: float | None
    currency: str
    moq: int | None
    status: str
    specifications: str
    material: str
    dimensions: str
    packaging: str
    image_url: str
    created_at: str
    updated_at: str

    @property
    def price_display(self) -> str:
        """展示用价格；未填写时明确显示“未填写”，不推测价格。"""
        if self.price is None:
            return "未填写"
        return f"{self.currency} {self.price:,.2f}"

    @property
    def moq_display(self) -> str:
        return "未填写" if self.moq is None else str(self.moq)

    @property
    def display_label(self) -> str:
        """下拉选择时使用的展示名，SKU 为空时不留下多余符号。"""
        return f"{self.name}（{self.sku}）" if self.sku else self.name


def initialize_database(db_path: Path | str | None = None) -> None:
    """创建产品目录表与索引；重复调用安全。"""
    try:
        with closing(connect(db_path)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS products (
                        product_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        sku TEXT NOT NULL DEFAULT '',
                        category TEXT NOT NULL DEFAULT '',
                        description TEXT NOT NULL DEFAULT '',
                        price REAL,
                        currency TEXT NOT NULL DEFAULT 'USD',
                        moq INTEGER,
                        status TEXT NOT NULL DEFAULT '草稿',
                        specifications TEXT NOT NULL DEFAULT '',
                        material TEXT NOT NULL DEFAULT '',
                        dimensions TEXT NOT NULL DEFAULT '',
                        packaging TEXT NOT NULL DEFAULT '',
                        image_url TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                existing_columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(products)")
                }
                migrations = {
                    "image_url": (
                        "ALTER TABLE products ADD COLUMN "
                        "image_url TEXT NOT NULL DEFAULT ''"
                    ),
                    "specifications": (
                        "ALTER TABLE products ADD COLUMN "
                        "specifications TEXT NOT NULL DEFAULT ''"
                    ),
                }
                for column, statement in migrations.items():
                    if column not in existing_columns:
                        connection.execute(statement)

                # SKU 可选，但一旦填写就必须唯一（忽略大小写与首尾空格）。
                connection.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_products_sku_unique
                    ON products (sku COLLATE NOCASE)
                    WHERE TRIM(sku) <> ''
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_products_category "
                    "ON products (category)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_products_status "
                    "ON products (status)"
                )
    except sqlite3.Error as exc:
        raise ProductError("无法初始化产品目录数据库。") from exc


def _clean_text(value: object, *, field: str, max_length: int, required: bool) -> str:
    text = "" if value is None else str(value).strip()
    if required and not text:
        raise ProductError(f"{field}不能为空。")
    if len(text) > max_length:
        raise ProductError(f"{field}过长，请控制在 {max_length} 个字符以内。")
    return text


def parse_price(value: object) -> float | None:
    """把价格输入转换为非负浮点数；留空返回 None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        price = float(value)
        if price < 0:
            raise ProductError("价格不能为负数。")
        return price
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        price = float(text)
    except ValueError as exc:
        raise ProductError("价格必须是数字，例如 180 或 180.50。") from exc
    if price < 0:
        raise ProductError("价格不能为负数。")
    if price != price or price in (float("inf"), float("-inf")):
        raise ProductError("价格必须是有效数字。")
    return price


def parse_moq(value: object) -> int | None:
    """把 MOQ 输入转换为非负整数；留空返回 None。"""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ProductError("最小起订量必须是整数。")
    if isinstance(value, int):
        quantity = value
    else:
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        if not text.isdigit():
            raise ProductError("最小起订量必须是非负整数，例如 50。")
        quantity = int(text)
    if quantity < 0:
        raise ProductError("最小起订量不能为负数。")
    return quantity


def _validate_choice(value: str, options: tuple[str, ...], field: str) -> str:
    if value not in options:
        raise ProductError(f"{field}无效，请选择：{'、'.join(options)}。")
    return value


def _validate_product_input(
    *,
    name: object,
    sku: object,
    category: object,
    description: object,
    price: object,
    currency: object,
    moq: object,
    status: object,
    specifications: object,
    material: object,
    dimensions: object,
    packaging: object,
    image_url: object,
) -> dict[str, object]:
    """统一校验并归一化产品字段，返回可直接写入数据库的字典。"""
    return {
        "name": _clean_text(
            name, field="产品名称", max_length=MAX_NAME_LENGTH, required=True
        ),
        "sku": _clean_text(
            sku, field="SKU", max_length=MAX_SKU_LENGTH, required=False
        ),
        "category": _clean_text(
            category,
            field="产品分类",
            max_length=MAX_SHORT_FIELD_LENGTH,
            required=False,
        ),
        "description": _clean_text(
            description,
            field="产品描述",
            max_length=MAX_TEXT_LENGTH,
            required=False,
        ),
        "price": parse_price(price),
        "currency": _validate_choice(
            _clean_text(
                currency,
                field="货币单位",
                max_length=10,
                required=False,
            )
            or DEFAULT_CURRENCY,
            CURRENCIES,
            "货币单位",
        ),
        "moq": parse_moq(moq),
        "status": _validate_choice(
            _clean_text(
                status, field="产品状态", max_length=20, required=False
            )
            or DEFAULT_STATUS,
            PRODUCT_STATUSES,
            "产品状态",
        ),
        "specifications": _clean_text(
            specifications,
            field="产品规格",
            max_length=MAX_TEXT_LENGTH,
            required=False,
        ),
        "material": _clean_text(
            material,
            field="材质",
            max_length=MAX_SHORT_FIELD_LENGTH,
            required=False,
        ),
        "dimensions": _clean_text(
            dimensions,
            field="尺寸",
            max_length=MAX_SHORT_FIELD_LENGTH,
            required=False,
        ),
        "packaging": _clean_text(
            packaging,
            field="包装",
            max_length=MAX_SHORT_FIELD_LENGTH,
            required=False,
        ),
        "image_url": _clean_text(
            image_url,
            field="图片链接",
            max_length=MAX_SHORT_FIELD_LENGTH,
            required=False,
        ),
    }


def create_product(
    name: object,
    *,
    sku: object = "",
    category: object = "",
    description: object = "",
    price: object = "",
    currency: object = DEFAULT_CURRENCY,
    moq: object = "",
    status: object = DEFAULT_STATUS,
    specifications: object = "",
    material: object = "",
    dimensions: object = "",
    packaging: object = "",
    image_url: object = "",
    db_path: Path | str | None = None,
) -> str:
    """新增产品并返回产品 ID；SKU 重复时抛出可展示的错误。"""
    fields = _validate_product_input(
        name=name,
        sku=sku,
        category=category,
        description=description,
        price=price,
        currency=currency,
        moq=moq,
        status=status,
        specifications=specifications,
        material=material,
        dimensions=dimensions,
        packaging=packaging,
        image_url=image_url,
    )
    initialize_database(db_path)
    product_id = str(uuid.uuid4())
    timestamp = now_timestamp()
    try:
        with closing(connect(db_path)) as connection:
            connection.execute(
                """
                INSERT INTO products (
                    product_id, name, sku, category, description, price,
                    currency, moq, status, specifications, material,
                    dimensions, packaging, image_url, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product_id,
                    fields["name"],
                    fields["sku"],
                    fields["category"],
                    fields["description"],
                    fields["price"],
                    fields["currency"],
                    fields["moq"],
                    fields["status"],
                    fields["specifications"],
                    fields["material"],
                    fields["dimensions"],
                    fields["packaging"],
                    fields["image_url"],
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()
    except sqlite3.IntegrityError as exc:
        raise ProductError(
            f"SKU“{fields['sku']}”已被其他产品使用，请更换或留空。"
        ) from exc
    except sqlite3.Error as exc:
        raise ProductError("保存产品失败，请检查数据库文件状态。") from exc
    return product_id


def update_product(
    product_id: str,
    *,
    name: object,
    sku: object = "",
    category: object = "",
    description: object = "",
    price: object = "",
    currency: object = DEFAULT_CURRENCY,
    moq: object = "",
    status: object = DEFAULT_STATUS,
    specifications: object = "",
    material: object = "",
    dimensions: object = "",
    packaging: object = "",
    image_url: object = "",
    db_path: Path | str | None = None,
) -> bool:
    """更新产品；返回是否确实更新了一条记录。"""
    fields = _validate_product_input(
        name=name,
        sku=sku,
        category=category,
        description=description,
        price=price,
        currency=currency,
        moq=moq,
        status=status,
        specifications=specifications,
        material=material,
        dimensions=dimensions,
        packaging=packaging,
        image_url=image_url,
    )
    initialize_database(db_path)
    try:
        with closing(connect(db_path)) as connection:
            cursor = connection.execute(
                """
                UPDATE products
                SET name = ?, sku = ?, category = ?, description = ?, price = ?,
                    currency = ?, moq = ?, status = ?, specifications = ?,
                    material = ?, dimensions = ?, packaging = ?, image_url = ?,
                    updated_at = ?
                WHERE product_id = ?
                """,
                (
                    fields["name"],
                    fields["sku"],
                    fields["category"],
                    fields["description"],
                    fields["price"],
                    fields["currency"],
                    fields["moq"],
                    fields["status"],
                    fields["specifications"],
                    fields["material"],
                    fields["dimensions"],
                    fields["packaging"],
                    fields["image_url"],
                    now_timestamp(),
                    product_id,
                ),
            )
            connection.commit()
            return cursor.rowcount > 0
    except sqlite3.IntegrityError as exc:
        raise ProductError(
            f"SKU“{fields['sku']}”已被其他产品使用，请更换或留空。"
        ) from exc
    except sqlite3.Error as exc:
        raise ProductError("更新产品失败，请检查数据库文件状态。") from exc


def _row_to_product(row: sqlite3.Row) -> Product:
    return Product(
        product_id=row["product_id"],
        name=row["name"],
        sku=row["sku"],
        category=row["category"],
        description=row["description"],
        price=row["price"],
        currency=row["currency"],
        moq=row["moq"],
        status=row["status"],
        specifications=row["specifications"],
        material=row["material"],
        dimensions=row["dimensions"],
        packaging=row["packaging"],
        image_url=row["image_url"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


_PRODUCT_COLUMNS = (
    "product_id, name, sku, category, description, price, currency, moq, "
    "status, specifications, material, dimensions, packaging, image_url, "
    "created_at, updated_at"
)


def list_products(
    db_path: Path | str | None = None,
    *,
    keyword: str = "",
    category: str | None = None,
    status: str | None = None,
) -> list[Product]:
    """按关键词、分类和状态筛选产品，按更新时间从新到旧返回。"""
    if status is not None and status not in PRODUCT_STATUSES:
        raise ProductError("无效的产品状态。")
    initialize_database(db_path)
    clauses: list[str] = []
    parameters: list[str] = []
    if keyword.strip():
        pattern = f"%{keyword.strip()}%"
        clauses.append(
            "(name LIKE ? COLLATE NOCASE OR sku LIKE ? COLLATE NOCASE "
            "OR category LIKE ? COLLATE NOCASE "
            "OR description LIKE ? COLLATE NOCASE)"
        )
        parameters.extend([pattern] * 4)
    if category:
        clauses.append("category = ?")
        parameters.append(category)
    if status is not None:
        clauses.append("status = ?")
        parameters.append(status)
    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    try:
        with closing(connect(db_path)) as connection:
            rows = connection.execute(
                f"""
                SELECT {_PRODUCT_COLUMNS}
                FROM products
                {where_clause}
                ORDER BY updated_at DESC, rowid DESC
                """,
                parameters,
            ).fetchall()
    except sqlite3.Error as exc:
        raise ProductError("读取产品目录失败。") from exc
    return [_row_to_product(row) for row in rows]


def get_product(
    product_id: str, db_path: Path | str | None = None
) -> Product | None:
    """按 ID 读取单个产品；不存在时返回 None。"""
    initialize_database(db_path)
    try:
        with closing(connect(db_path)) as connection:
            row = connection.execute(
                f"SELECT {_PRODUCT_COLUMNS} FROM products WHERE product_id = ?",
                (product_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise ProductError("读取产品详情失败。") from exc
    return _row_to_product(row) if row else None


def delete_product(product_id: str, db_path: Path | str | None = None) -> bool:
    """删除产品；返回是否确实删除了一条记录。"""
    initialize_database(db_path)
    try:
        with closing(connect(db_path)) as connection:
            cursor = connection.execute(
                "DELETE FROM products WHERE product_id = ?", (product_id,)
            )
            connection.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as exc:
        raise ProductError("删除产品失败。") from exc


def list_categories(db_path: Path | str | None = None) -> list[str]:
    """返回已有产品分类，用于筛选下拉框，只包含真实存在的分类。"""
    initialize_database(db_path)
    try:
        with closing(connect(db_path)) as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT TRIM(category) AS category
                FROM products
                WHERE TRIM(category) <> ''
                ORDER BY category COLLATE NOCASE ASC
                """
            ).fetchall()
    except sqlite3.Error as exc:
        raise ProductError("读取产品分类失败。") from exc
    return [row["category"] for row in rows]


def get_product_stats(db_path: Path | str | None = None) -> dict[str, int]:
    """统计产品总数及各状态数量，全部来自真实记录。"""
    initialize_database(db_path)
    try:
        with closing(connect(db_path)) as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = '在售' THEN 1 ELSE 0 END) AS active,
                    SUM(CASE WHEN status = '停售' THEN 1 ELSE 0 END) AS discontinued,
                    SUM(CASE WHEN status = '草稿' THEN 1 ELSE 0 END) AS draft
                FROM products
                """
            ).fetchone()
    except sqlite3.Error as exc:
        raise ProductError("统计产品目录失败。") from exc
    return {
        "total": row["total"] or 0,
        "active": row["active"] or 0,
        "discontinued": row["discontinued"] or 0,
        "draft": row["draft"] or 0,
    }


# ---------------------------------------------------------------------------
# 供 AI 助手复用的产品检索：只返回数据库中的真实记录。
# ---------------------------------------------------------------------------
# 中文与英文的常见停用词，避免把“请帮我介绍产品”里的词当作产品关键词。
_QUERY_STOPWORDS = {
    "the", "and", "for", "with", "you", "your", "our", "please", "help", "can",
    "could", "would", "what", "which", "this", "that", "about", "tell", "give",
    "introduce", "quote", "price", "moq", "product", "products", "catalog",
    "请问", "帮我", "介绍", "一下", "这个", "那个", "产品", "价格", "多少",
    "规格", "客户", "询盘", "邮件", "回复", "建议", "分析", "优化", "整理",
}


def extract_keywords(text: str, limit: int = 6) -> list[str]:
    """从自由文本中提取候选关键词，用于产品目录检索。"""
    tokens = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", text or "")
    keywords: list[str] = []
    for token in tokens:
        lowered = token.lower()
        if len(lowered) < 2 or lowered in _QUERY_STOPWORDS:
            continue
        if lowered not in [item.lower() for item in keywords]:
            keywords.append(token)
        if len(keywords) >= limit:
            break
    return keywords


def find_products_for_query(
    text: str,
    *,
    limit: int = 3,
    db_path: Path | str | None = None,
) -> list[Product]:
    """在真实产品目录中查找与用户文本相关的产品记录。

    先判断产品名称/SKU/分类是否直接出现在用户文本中（中英文都可靠），
    再用关键词检索补充英文同义词等场景；找不到时返回空列表，
    由调用方向用户说明“数据库中没有找到匹配记录”，而不是编造产品。
    """
    normalized = (text or "").strip().lower()
    matches: list[Product] = []
    seen: set[str] = set()
    if normalized:
        for product in list_products(db_path):
            candidates = (product.name, product.sku, product.category)
            if any(
                candidate and candidate.lower() in normalized
                for candidate in candidates
            ):
                matches.append(product)
                seen.add(product.product_id)
                if len(matches) >= limit:
                    return matches
        if matches:
            return matches
        for keyword in extract_keywords(text):
            for product in list_products(db_path, keyword=keyword):
                if product.product_id in seen:
                    continue
                seen.add(product.product_id)
                matches.append(product)
                if len(matches) >= limit:
                    return matches
    return matches
