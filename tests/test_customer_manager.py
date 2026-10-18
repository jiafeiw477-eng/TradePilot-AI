from __future__ import annotations

from datetime import date

import pytest

import services.inquiry_history as inquiry_history
from services.customer_manager import (
    CUSTOMER_STATUSES,
    CustomerError,
    archive_customer,
    create_customer,
    delete_customer,
    find_customers_for_query,
    get_customer,
    get_customer_stats,
    initialize_database,
    list_countries,
    list_customers,
    restore_customer,
    update_customer,
    validate_email,
)


def test_database_is_created_idempotently(tmp_path) -> None:
    db_path = tmp_path / "data" / "customers.sqlite3"

    initialize_database(db_path)
    initialize_database(db_path)

    assert db_path.is_file()


def test_create_and_read_customer_keeps_real_values(tmp_path) -> None:
    db_path = tmp_path / "customers.sqlite3"

    customer_id = create_customer(
        "Möbel GmbH",
        contact_name="Anna Schmidt",
        email="anna@example.com",
        phone="+49 30 1234",
        country="Germany",
        website="https://example.com",
        source="展会",
        level="重点客户",
        status="跟进中",
        notes="关注橡木餐桌",
        last_contact_date="2026-10-01",
        db_path=db_path,
    )

    customer = get_customer(customer_id, db_path)
    assert customer is not None
    assert customer.company_name == "Möbel GmbH"
    assert customer.display_label == "Möbel GmbH（Anna Schmidt）"
    assert customer.last_contact_date == "2026-10-01"
    assert customer.level == "重点客户"
    assert customer.archived is False


def test_company_name_is_required_and_email_is_validated(tmp_path) -> None:
    db_path = tmp_path / "customers.sqlite3"

    with pytest.raises(CustomerError, match="公司名称不能为空"):
        create_customer("  ", db_path=db_path)
    with pytest.raises(CustomerError, match="邮箱格式无效"):
        create_customer("客户 A", email="not-an-email", db_path=db_path)
    with pytest.raises(CustomerError, match="客户等级无效"):
        create_customer("客户 A", level="超级客户", db_path=db_path)
    with pytest.raises(CustomerError, match="客户状态无效"):
        create_customer("客户 A", status="已下单", db_path=db_path)

    assert list_customers(db_path) == []


def test_empty_email_is_allowed_and_normalized(tmp_path) -> None:
    db_path = tmp_path / "customers.sqlite3"

    assert validate_email("") == ""
    assert validate_email(None) == ""
    customer_id = create_customer("仅电话客户", db_path=db_path)

    customer = get_customer(customer_id, db_path)
    assert customer is not None
    assert customer.email == ""


def test_date_input_object_is_accepted_for_last_contact(tmp_path) -> None:
    db_path = tmp_path / "customers.sqlite3"

    customer_id = create_customer(
        "日期客户", last_contact_date=date(2026, 9, 30), db_path=db_path
    )

    customer = get_customer(customer_id, db_path)
    assert customer is not None
    assert customer.last_contact_date == "2026-09-30"


def test_duplicate_company_and_email_is_rejected(tmp_path) -> None:
    db_path = tmp_path / "customers.sqlite3"
    create_customer("客户 A", email="a@example.com", db_path=db_path)

    with pytest.raises(CustomerError, match="已存在公司名称与邮箱完全相同"):
        create_customer("客户 A", email="a@example.com", db_path=db_path)

    assert len(list_customers(db_path)) == 1


def test_same_company_without_email_is_also_treated_as_duplicate(tmp_path) -> None:
    db_path = tmp_path / "customers.sqlite3"
    create_customer("同名客户", db_path=db_path)

    with pytest.raises(CustomerError, match="已存在公司名称与邮箱完全相同"):
        create_customer("同名客户", db_path=db_path)

    assert len(list_customers(db_path)) == 1


def test_same_email_with_different_company_is_allowed(tmp_path) -> None:
    db_path = tmp_path / "customers.sqlite3"

    create_customer("客户 A", email="shared@example.com", db_path=db_path)
    create_customer("客户 B", email="shared@example.com", db_path=db_path)

    assert len(list_customers(db_path)) == 2


def test_update_customer_and_missing_result(tmp_path) -> None:
    db_path = tmp_path / "customers.sqlite3"
    customer_id = create_customer("旧公司", db_path=db_path)

    assert update_customer(
        customer_id,
        company_name="新公司",
        contact_name="李工",
        country="Spain",
        level="潜在重点",
        status="已成交",
        db_path=db_path,
    ) is True

    customer = get_customer(customer_id, db_path)
    assert customer is not None
    assert customer.company_name == "新公司"
    assert customer.country == "Spain"
    assert customer.status == "已成交"
    assert update_customer("not-exists", company_name="任意", db_path=db_path) is False


def test_archive_hides_customer_from_default_list_and_can_restore(tmp_path) -> None:
    db_path = tmp_path / "customers.sqlite3"
    customer_id = create_customer("归档客户", db_path=db_path)

    assert archive_customer(customer_id, db_path) is True
    assert list_customers(db_path) == []
    assert [item.company_name for item in list_customers(db_path, include_archived=True)] == [
        "归档客户"
    ]

    assert restore_customer(customer_id, db_path) is True
    assert len(list_customers(db_path)) == 1


def test_delete_customer_without_inquiries_removes_record(tmp_path) -> None:
    db_path = tmp_path / "customers.sqlite3"
    customer_id = create_customer("无询盘客户", db_path=db_path)

    assert delete_customer(customer_id, db_path) == "deleted"
    assert get_customer(customer_id, db_path) is None
    assert delete_customer(customer_id, db_path) == "missing"


def test_delete_customer_with_inquiries_archives_instead_of_cascading(
    tmp_path,
) -> None:
    db_path = tmp_path / "shared.sqlite3"
    customer_id = create_customer("有询盘客户", db_path=db_path)
    record_id = inquiry_history.save_inquiry_record(
        "Please quote 200 dining tables.",
        _sample_analysis(),
        db_path,
        customer_id=customer_id,
    )

    assert delete_customer(customer_id, db_path) == "archived"

    # 客户资料保留（归档），历史询盘必须完整保留，不做级联删除。
    archived = get_customer(customer_id, db_path)
    assert archived is not None
    assert archived.archived is True
    assert len(inquiry_history.list_inquiry_records(db_path)) == 1
    assert inquiry_history.get_inquiry_record(record_id, db_path) is not None


def test_list_customers_supports_filters_and_countries(tmp_path) -> None:
    db_path = tmp_path / "customers.sqlite3"
    create_customer("德国客户", country="Germany", level="重点客户", status="跟进中", db_path=db_path)
    create_customer("西班牙客户", country="Spain", level="普通", status="已成交", db_path=db_path)

    assert [item.company_name for item in list_customers(db_path, country="Spain")] == [
        "西班牙客户"
    ]
    assert [item.company_name for item in list_customers(db_path, level="重点客户")] == [
        "德国客户"
    ]
    assert [item.company_name for item in list_customers(db_path, status="已成交")] == [
        "西班牙客户"
    ]
    assert [item.company_name for item in list_customers(db_path, keyword="西班牙")] == [
        "西班牙客户"
    ]
    assert list_countries(db_path) == ["Germany", "Spain"]
    with pytest.raises(CustomerError, match="无效的客户等级"):
        list_customers(db_path, level="超级客户")


def test_customer_stats_reflect_real_records(tmp_path) -> None:
    db_path = tmp_path / "customers.sqlite3"
    create_customer("重点甲乙", level="重点客户", status="已成交", db_path=db_path)
    create_customer("普通客户", db_path=db_path)
    archived_id = create_customer("归档客户", db_path=db_path)
    archive_customer(archived_id, db_path)

    assert get_customer_stats(db_path) == {
        "total": 3,
        "archived": 1,
        "key_accounts": 1,
        "won": 1,
    }
    assert set(CUSTOMER_STATUSES) == {"潜在客户", "跟进中", "已成交", "暂停跟进"}


def test_find_customers_for_query_matches_and_returns_empty(tmp_path) -> None:
    db_path = tmp_path / "customers.sqlite3"
    create_customer("Möbel GmbH", contact_name="Anna", db_path=db_path)

    assert [
        item.company_name
        for item in find_customers_for_query("请整理 Möbel GmbH 的跟进建议", db_path=db_path)
    ] == ["Möbel GmbH"]
    assert find_customers_for_query("请整理未知客户的跟进建议", db_path=db_path) == []
    assert find_customers_for_query("   ", db_path=db_path) == []


def _sample_analysis():
    from services.inquiry_ai import InquiryAIAnalysis

    return InquiryAIAnalysis(
        translation_zh="我们需要 200 张餐桌。",
        customer_needs={
            "product": "餐桌",
            "specifications": "橡木",
            "quantity": "200 张",
            "target_price": "未提供",
            "delivery_time": "45 天",
            "trade_terms": "FOB",
            "other_requirements": "FSC 认证",
        },
        intent_level="高",
        intent_rationale="客户询问报价和交付；此判断仅供参考。",
        risks_and_questions=["请确认付款条件。"],
        reply_draft_en="Dear Customer, thank you for your inquiry.",
        reply_draft_zh="尊敬的客户，感谢您的询盘。",
        follow_up_suggestions=["确认付款条件。"],
    )
