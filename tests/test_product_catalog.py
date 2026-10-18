from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from services.product_catalog import (
    PRODUCT_STATUSES,
    ProductError,
    create_product,
    delete_product,
    extract_keywords,
    find_products_for_query,
    get_product,
    get_product_stats,
    initialize_database,
    list_categories,
    list_products,
    parse_moq,
    parse_price,
    update_product,
)


def test_database_and_sku_index_are_created_idempotently(tmp_path) -> None:
    db_path = tmp_path / "data" / "catalog.sqlite3"

    initialize_database(db_path)
    initialize_database(db_path)

    assert db_path.is_file()
    with closing(sqlite3.connect(db_path)) as connection:
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
    assert "idx_products_sku_unique" in indexes


def test_create_and_read_product_keeps_real_values(tmp_path) -> None:
    db_path = tmp_path / "catalog.sqlite3"

    product_id = create_product(
        "橡木餐桌",
        sku="TBL-001",
        category="家具",
        description="实木餐桌",
        price="180.50",
        currency="USD",
        moq="50",
        status="在售",
        material="橡木",
        dimensions="140x80x75cm",
        packaging="木箱",
        db_path=db_path,
    )

    product = get_product(product_id, db_path)
    assert product is not None
    assert product.name == "橡木餐桌"
    assert product.price == pytest.approx(180.5)
    assert product.price_display == "USD 180.50"
    assert product.moq == 50
    assert product.moq_display == "50"
    assert product.display_label == "橡木餐桌（TBL-001）"
    assert product.status == "在售"


def test_missing_optional_fields_are_not_guessed(tmp_path) -> None:
    db_path = tmp_path / "catalog.sqlite3"

    product_id = create_product("未定价产品", db_path=db_path)

    product = get_product(product_id, db_path)
    assert product is not None
    assert product.price is None
    assert product.price_display == "未填写"
    assert product.moq is None
    assert product.moq_display == "未填写"
    # 未指定状态时回落到“草稿”，不会自动当作在售。
    assert product.status == "草稿"
    assert product.currency == "USD"


def test_duplicate_sku_is_rejected_case_insensitively(tmp_path) -> None:
    db_path = tmp_path / "catalog.sqlite3"
    create_product("产品 A", sku="ABC-1", db_path=db_path)

    with pytest.raises(ProductError, match="已被其他产品使用"):
        create_product("产品 B", sku="abc-1", db_path=db_path)

    assert len(list_products(db_path)) == 1


def test_blank_sku_can_repeat(tmp_path) -> None:
    db_path = tmp_path / "catalog.sqlite3"

    create_product("产品 A", db_path=db_path)
    create_product("产品 B", db_path=db_path)

    assert len(list_products(db_path)) == 2


def test_product_name_is_required_and_price_is_validated(tmp_path) -> None:
    db_path = tmp_path / "catalog.sqlite3"

    with pytest.raises(ProductError, match="产品名称不能为空"):
        create_product("   ", db_path=db_path)
    with pytest.raises(ProductError, match="价格必须是数字"):
        create_product("产品", price="价格面议", db_path=db_path)
    with pytest.raises(ProductError, match="价格不能为负数"):
        create_product("产品", price="-1", db_path=db_path)
    with pytest.raises(ProductError, match="最小起订量必须是非负整数"):
        create_product("产品", moq="50.5", db_path=db_path)
    with pytest.raises(ProductError, match="货币单位无效"):
        create_product("产品", currency="XYZ", db_path=db_path)
    with pytest.raises(ProductError, match="产品状态无效"):
        create_product("产品", status="热卖", db_path=db_path)

    assert list_products(db_path) == []


def test_price_and_moq_parsing_helpers() -> None:
    assert parse_price("") is None
    assert parse_price("1,200.75") == pytest.approx(1200.75)
    assert parse_price(180) == pytest.approx(180.0)
    assert parse_moq("") is None
    assert parse_moq("1,000") == 1000
    assert parse_moq(50) == 50
    with pytest.raises(ProductError):
        parse_moq(True)


def test_update_product_changes_fields_and_reports_missing(tmp_path) -> None:
    db_path = tmp_path / "catalog.sqlite3"
    product_id = create_product("旧名称", sku="SKU-1", price="10", db_path=db_path)

    updated = update_product(
        product_id,
        name="新名称",
        sku="SKU-1",
        category="家具",
        price="99.9",
        moq="20",
        status="在售",
        db_path=db_path,
    )

    assert updated is True
    product = get_product(product_id, db_path)
    assert product is not None
    assert product.name == "新名称"
    assert product.price == pytest.approx(99.9)
    assert product.moq == 20
    assert product.status == "在售"
    assert update_product(
        "not-exists", name="任意", db_path=db_path
    ) is False


def test_delete_product_reports_result(tmp_path) -> None:
    db_path = tmp_path / "catalog.sqlite3"
    product_id = create_product("待删除", db_path=db_path)

    assert delete_product(product_id, db_path) is True
    assert delete_product(product_id, db_path) is False
    assert list_products(db_path) == []


def test_list_products_supports_filters_and_categories(tmp_path) -> None:
    db_path = tmp_path / "catalog.sqlite3"
    create_product("橡木餐桌", sku="TBL-1", category="家具", status="在售", db_path=db_path)
    create_product("金属餐椅", sku="CHR-1", category="家具", status="草稿", db_path=db_path)
    create_product("包装纸箱", sku="BOX-1", category="包装", status="停售", db_path=db_path)

    assert {item.name for item in list_products(db_path, category="家具")} == {
        "橡木餐桌",
        "金属餐椅",
    }
    assert [item.name for item in list_products(db_path, status="停售")] == ["包装纸箱"]
    assert [item.name for item in list_products(db_path, keyword="chr-1")] == ["金属餐椅"]
    assert list_categories(db_path) == ["包装", "家具"]
    with pytest.raises(ProductError, match="无效的产品状态"):
        list_products(db_path, status="热卖")


def test_product_stats_count_each_status(tmp_path) -> None:
    db_path = tmp_path / "catalog.sqlite3"
    create_product("在售品", status="在售", db_path=db_path)
    create_product("停售品", status="停售", db_path=db_path)
    create_product("草稿品", status="草稿", db_path=db_path)

    assert get_product_stats(db_path) == {
        "total": 3,
        "active": 1,
        "discontinued": 1,
        "draft": 1,
    }
    assert set(PRODUCT_STATUSES) == {"在售", "停售", "草稿"}


def test_extract_keywords_drops_stopwords() -> None:
    keywords = extract_keywords("介绍 dining table 价格")

    assert keywords == ["dining", "table"]
    assert extract_keywords("a 的") == []


def test_find_products_for_query_matches_and_returns_empty(tmp_path) -> None:
    db_path = tmp_path / "catalog.sqlite3"
    create_product("橡木餐桌", sku="TBL-001", category="家具", db_path=db_path)
    create_product("金属餐椅", sku="CHR-001", category="家具", db_path=db_path)

    # 产品名直接出现在文本中时必须能命中（中文检索的可靠路径）。
    matches = find_products_for_query("请介绍橡木餐桌的规格", db_path=db_path)
    assert [item.name for item in matches] == ["橡木餐桌"]

    # SKU 命中同样有效。
    assert [item.sku for item in find_products_for_query("TBL-001", db_path=db_path)] == [
        "TBL-001"
    ]

    # 数据库中没有的产品必须返回空列表，由调用方说明“未找到”，不得编造。
    assert find_products_for_query("请介绍大理石茶几", db_path=db_path) == []


def test_find_products_for_query_respects_limit(tmp_path) -> None:
    db_path = tmp_path / "catalog.sqlite3"
    for index in range(5):
        create_product(f"餐椅 {index}", sku=f"CHR-{index}", db_path=db_path)

    assert len(find_products_for_query("餐椅", limit=2, db_path=db_path)) == 2
