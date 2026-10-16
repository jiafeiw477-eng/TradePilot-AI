from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from services.inquiry_ai import InquiryAIAnalysis
from services.inquiry_history import (
    InquiryHistoryError,
    delete_inquiry_record,
    get_inquiry_history_stats,
    initialize_database,
    list_inquiry_records,
    save_inquiry_record,
    update_inquiry_follow_up,
)


def sample_analysis() -> InquiryAIAnalysis:
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


def test_database_is_created_and_empty_query_is_supported(tmp_path) -> None:
    db_path = tmp_path / "data" / "history.sqlite3"

    initialize_database(db_path)

    assert db_path.is_file()
    assert list_inquiry_records(db_path) == []


def test_database_errors_are_reported_without_touching_other_paths(tmp_path) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("keep", encoding="utf-8")
    invalid_db_path = blocker / "history.sqlite3"

    with pytest.raises(InquiryHistoryError, match="无法打开"):
        list_inquiry_records(invalid_db_path)

    assert blocker.read_text(encoding="utf-8") == "keep"


def test_records_persist_in_time_order_and_can_be_deleted(tmp_path) -> None:
    db_path = tmp_path / "history.sqlite3"
    analysis = sample_analysis()

    first_id = save_inquiry_record("First inquiry", analysis, db_path)
    second_id = save_inquiry_record("Second inquiry", analysis, db_path)
    records_after_reopen = list_inquiry_records(db_path)

    assert records_after_reopen[0].record_id == second_id
    assert records_after_reopen[1].record_id == first_id
    assert records_after_reopen[0].original_inquiry == "Second inquiry"
    assert records_after_reopen[0].translation_zh == analysis.translation_zh
    assert records_after_reopen[0].customer_needs == analysis.customer_needs
    assert records_after_reopen[0].intent_level == analysis.intent_level
    assert records_after_reopen[0].intent_rationale == analysis.intent_rationale
    assert records_after_reopen[0].risks_and_questions == (
        analysis.risks_and_questions
    )
    assert records_after_reopen[0].reply_draft_en == analysis.reply_draft_en
    assert records_after_reopen[0].follow_up_suggestions == (
        analysis.follow_up_suggestions
    )

    assert delete_inquiry_record(second_id, db_path)
    assert not delete_inquiry_record(second_id, db_path)
    remaining = list_inquiry_records(db_path)
    assert [record.record_id for record in remaining] == [first_id]


def test_old_database_is_migrated_without_losing_existing_record(tmp_path) -> None:
    db_path = tmp_path / "old_history.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE inquiry_history (
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
                follow_up_suggestions_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO inquiry_history VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "old-record",
                "2026-01-01T10:00:00+08:00",
                "Please quote 10 tables.",
                "请提供 10 张桌子的报价。",
                '{"product": "餐桌"}',
                "高",
                "明确询价",
                "[]",
                "Dear Customer",
                "尊敬的客户",
                '["回复客户"]',
            ),
        )

    records = list_inquiry_records(db_path)
    with closing(sqlite3.connect(db_path)) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(inquiry_history)")
        }

    assert len(records) == 1
    assert records[0].record_id == "old-record"
    assert records[0].original_inquiry == "Please quote 10 tables."
    assert records[0].follow_up_status == "待跟进"
    assert records[0].priority == "中"
    assert records[0].follow_up_notes == ""
    assert records[0].next_follow_up_date is None
    assert {
        "follow_up_status",
        "priority",
        "follow_up_notes",
        "next_follow_up_date",
    }.issubset(columns)


def test_management_fields_persist_search_filter_and_statistics(tmp_path) -> None:
    db_path = tmp_path / "management.sqlite3"
    first_id = save_inquiry_record(
        "Please quote oak dining tables.", sample_analysis(), db_path
    )
    second_id = save_inquiry_record(
        "Please quote office desks.", sample_analysis(), db_path
    )

    assert update_inquiry_follow_up(
        first_id,
        status="跟进中",
        priority="高",
        notes="已发送报价，周五联系",
        next_follow_up_date="2026-10-23",
        db_path=db_path,
    )
    assert update_inquiry_follow_up(
        second_id,
        status="已完成",
        priority="低",
        notes="客户暂不采购",
        next_follow_up_date=None,
        db_path=db_path,
    )

    reopened_records = list_inquiry_records(db_path)
    updated_first = next(row for row in reopened_records if row.record_id == first_id)
    assert updated_first.follow_up_status == "跟进中"
    assert updated_first.priority == "高"
    assert updated_first.follow_up_notes == "已发送报价，周五联系"
    assert updated_first.next_follow_up_date == "2026-10-23"

    stats = get_inquiry_history_stats(db_path)
    assert (stats.total, stats.pending, stats.in_progress) == (2, 0, 1)
    assert (stats.completed, stats.high_priority) == (1, 1)
    assert [
        row.record_id
        for row in list_inquiry_records(
            db_path,
            keyword="oak",
            status="跟进中",
            priority="高",
        )
    ] == [first_id]
    assert list_inquiry_records(db_path, keyword="does-not-exist") == []


@pytest.mark.parametrize(
    ("status", "priority", "follow_up_date", "message"),
    [
        ("未知", "中", None, "状态"),
        ("待跟进", "紧急", None, "优先级"),
        ("待跟进", "中", "2026-99-99", "日期"),
    ],
)
def test_management_update_rejects_invalid_values(
    tmp_path, status, priority, follow_up_date, message
) -> None:
    record_id = save_inquiry_record(
        "Please quote tables.", sample_analysis(), tmp_path / "invalid.sqlite3"
    )

    with pytest.raises(InquiryHistoryError, match=message):
        update_inquiry_follow_up(
            record_id,
            status=status,
            priority=priority,
            notes="",
            next_follow_up_date=follow_up_date,
            db_path=tmp_path / "invalid.sqlite3",
        )
