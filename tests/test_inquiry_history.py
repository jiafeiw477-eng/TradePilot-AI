from __future__ import annotations

import csv
import io
import sqlite3
from contextlib import closing
from datetime import date, timedelta

import pytest

from services.inquiry_ai import InquiryAIAnalysis
from services.inquiry_history import (
    CSV_EXPORT_HEADERS,
    RECENT_WINDOW_DAYS,
    InquiryHistoryError,
    build_business_insights,
    build_inquiry_records_csv,
    count_overdue_follow_ups,
    delete_inquiry_record,
    export_inquiry_records_csv,
    get_inquiry_analysis_summary,
    get_inquiry_country_distribution,
    get_inquiry_date_bounds,
    get_inquiry_history_stats,
    get_inquiry_missing_field_stats,
    get_inquiry_product_distribution,
    get_inquiry_status_distribution,
    get_inquiry_trend,
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


# ---------------------------------------------------------------------------
# 业务数据分析
# ---------------------------------------------------------------------------


def save_with_created_at(
    db_path, created_at: str, *, inquiry: str = "Please quote tables."
) -> str:
    """保存记录并改写 created_at，用于构造跨日期、跨时区的真实测试数据。"""
    record_id = save_inquiry_record(inquiry, sample_analysis(), db_path)
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute(
            "UPDATE inquiry_history SET created_at = ? WHERE record_id = ?",
            (created_at, record_id),
        )
        connection.commit()
    return record_id


def test_analysis_summary_reflects_real_records_and_date_filter(tmp_path) -> None:
    db_path = tmp_path / "analytics.sqlite3"
    save_with_created_at(db_path, "2026-09-30T09:00:00+08:00")
    newest = save_with_created_at(db_path, "2026-10-16T23:50:00+08:00")
    update_inquiry_follow_up(
        newest,
        status="已完成",
        priority="高",
        notes="成交",
        next_follow_up_date=None,
        db_path=db_path,
    )

    summary = get_inquiry_analysis_summary(db_path=db_path)
    assert (summary.total, summary.pending, summary.in_progress) == (2, 1, 0)
    assert (summary.completed, summary.high_priority) == (1, 1)
    assert (summary.other_status, summary.processed) == (0, 1)
    assert summary.completion_rate == pytest.approx(0.5)
    assert (summary.earliest_date, summary.latest_date) == (
        "2026-09-30",
        "2026-10-16",
    )
    assert get_inquiry_date_bounds(db_path) == ("2026-09-30", "2026-10-16")

    # 日期筛选同步作用于记录列表与核心指标，边界日期包含在内。
    filtered = get_inquiry_analysis_summary("2026-10-01", "2026-10-16", db_path)
    assert (filtered.total, filtered.completed) == (1, 1)
    assert filtered.completion_rate == pytest.approx(1.0)
    assert [
        record.record_id
        for record in list_inquiry_records(db_path, start_date="2026-10-01")
    ] == [newest]

    # 无数据区间与空数据库都不能出现除零错误。
    empty = get_inquiry_analysis_summary("2026-10-01", "2026-10-15", db_path)
    assert empty.total == 0
    assert empty.completion_rate is None
    assert empty.earliest_date is None
    brand_new = get_inquiry_analysis_summary(db_path=tmp_path / "brand-new.sqlite3")
    assert (brand_new.total, brand_new.completion_rate) == (0, None)


def test_trend_groups_by_local_day_and_week(tmp_path) -> None:
    db_path = tmp_path / "trend.sqlite3"
    save_with_created_at(db_path, "2026-10-07T09:00:00+08:00")
    save_with_created_at(db_path, "2026-10-16T02:30:00+08:00")
    save_with_created_at(db_path, "2026-10-16T23:50:00+08:00")

    # 带时区偏移的时间必须按本地日期归组，不能被 SQLite 按 UTC 归并而跨日。
    assert get_inquiry_trend("day", db_path=db_path) == [
        ("2026-10-07", 1),
        ("2026-10-16", 2),
    ]
    assert get_inquiry_trend("week", db_path=db_path) == [
        ("2026-10-05", 1),
        ("2026-10-12", 2),
    ]
    assert get_inquiry_trend("day", "2026-10-16", "2026-10-16", db_path) == [
        ("2026-10-16", 2)
    ]

    with pytest.raises(InquiryHistoryError, match="粒度"):
        get_inquiry_trend("month", db_path=db_path)


def test_unknown_status_is_reported_without_reclassification(tmp_path) -> None:
    db_path = tmp_path / "unknown-status.sqlite3"
    record_id = save_with_created_at(db_path, "2026-10-10T10:00:00+08:00")
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute(
            "UPDATE inquiry_history SET follow_up_status = ? WHERE record_id = ?",
            ("已回复", record_id),
        )
        connection.commit()

    assert get_inquiry_status_distribution(db_path=db_path) == [("已回复", 1)]

    summary = get_inquiry_analysis_summary(db_path=db_path)
    assert summary.total == 1
    assert (summary.pending, summary.in_progress, summary.completed) == (0, 0, 0)
    assert summary.other_status == 1


def test_product_country_and_missing_field_statistics(tmp_path) -> None:
    db_path = tmp_path / "dimensions.sqlite3"
    save_inquiry_record(
        "We are a furniture importer based in Germany. Please quote tables.",
        sample_analysis(),
        db_path,
    )
    sparse_analysis = InquiryAIAnalysis(
        translation_zh="需要办公椅。",
        customer_needs={
            "product": "办公椅",
            "specifications": "未提供",
            "quantity": "未提供",
            "target_price": "未提供",
            "delivery_time": "未提供",
            "trade_terms": "未提供",
            "other_requirements": "未提供",
        },
        intent_level="中",
        intent_rationale="仅供参考。",
        risks_and_questions=[],
        reply_draft_en="Dear Customer",
        reply_draft_zh="尊敬的客户",
        follow_up_suggestions=[],
    )
    save_inquiry_record("Please quote office chairs.", sparse_analysis, db_path)
    save_inquiry_record("No country is mentioned in this email.", sparse_analysis, db_path)

    product_distribution, identified = get_inquiry_product_distribution(db_path=db_path)
    assert identified == 3
    assert product_distribution == [("办公椅", 2), ("餐桌", 1)]

    # 国家只能来自邮件中的明确表述，其余记录记为未识别。
    distribution, identified_countries, scoped_total = (
        get_inquiry_country_distribution(db_path=db_path)
    )
    assert distribution == [("德国", 1)]
    assert (identified_countries, scoped_total) == (1, 3)

    missing = dict(get_inquiry_missing_field_stats(db_path=db_path))
    assert missing["产品"] == 0
    assert missing["目标价格"] == 3
    assert missing["规格"] == 2


def test_csv_export_uses_business_columns_and_handles_empty_data(tmp_path) -> None:
    empty_csv = export_inquiry_records_csv(db_path=tmp_path / "no-records.sqlite3")
    assert empty_csv.startswith("\ufeff")
    assert empty_csv.splitlines() == ["\ufeff" + ",".join(CSV_EXPORT_HEADERS)]

    db_path = tmp_path / "export.sqlite3"
    save_with_created_at(db_path, "2026-10-16T02:30:00+08:00")
    rows = export_inquiry_records_csv(db_path=db_path).splitlines()

    assert len(rows) == 2
    assert rows[0].lstrip("\ufeff") == ",".join(CSV_EXPORT_HEADERS)
    assert "餐桌" in rows[1]
    assert "未提供" in rows[1]
    assert "API" not in rows[0]


def test_csv_export_escapes_formula_like_business_text(tmp_path) -> None:
    analysis = sample_analysis()
    analysis_with_formula = InquiryAIAnalysis(
        translation_zh=analysis.translation_zh,
        customer_needs={
            **analysis.customer_needs,
            "target_price": "=HYPERLINK(\"https://example.invalid\")",
        },
        intent_level=analysis.intent_level,
        intent_rationale=analysis.intent_rationale,
        risks_and_questions=analysis.risks_and_questions,
        reply_draft_en=analysis.reply_draft_en,
        reply_draft_zh=analysis.reply_draft_zh,
        follow_up_suggestions=analysis.follow_up_suggestions,
    )
    db_path = tmp_path / "formula-export.sqlite3"
    save_inquiry_record(
        "=HYPERLINK(\"https://example.invalid\")",
        analysis_with_formula,
        db_path,
    )

    rows = list(
        csv.reader(io.StringIO(export_inquiry_records_csv(db_path=db_path).lstrip("\ufeff")))
    )
    exported = dict(zip(rows[0], rows[1]))

    assert exported["原始英文询盘"].startswith("'=")
    assert exported["目标价格"].startswith("'=")


def test_business_insights_are_rule_based_and_handle_empty_data(tmp_path) -> None:
    assert build_business_insights(
        get_inquiry_analysis_summary(db_path=tmp_path / "insights-empty.sqlite3")
    ) == [
        "当前筛选范围内没有询盘记录，暂无法生成业务洞察。"
        "可以调整日期范围或先完成一次询盘分析。"
    ]

    db_path = tmp_path / "insights.sqlite3"
    in_progress = save_with_created_at(db_path, "2026-10-10T09:00:00+08:00")
    completed = save_with_created_at(db_path, "2026-10-11T09:00:00+08:00")
    update_inquiry_follow_up(
        in_progress, status="跟进中", priority="高", notes="",
        next_follow_up_date=None, db_path=db_path,
    )
    update_inquiry_follow_up(
        completed, status="已完成", priority="中", notes="",
        next_follow_up_date=None, db_path=db_path,
    )

    insights = build_business_insights(
        get_inquiry_analysis_summary(db_path=db_path),
        status_distribution=get_inquiry_status_distribution(db_path=db_path),
        product_distribution=get_inquiry_product_distribution(db_path=db_path)[0],
        missing_field_stats=get_inquiry_missing_field_stats(db_path=db_path),
        trend=get_inquiry_trend("day", db_path=db_path),
    )
    assert any("跟进中询盘 1 条" in item for item in insights)
    assert any("已完成询盘 1 条" in item for item in insights)
    assert all(item.strip() for item in insights)


def test_overdue_follow_ups_and_invalid_date_range(tmp_path) -> None:
    db_path = tmp_path / "overdue.sqlite3"
    today = date(2026, 10, 17)
    overdue = save_with_created_at(db_path, "2026-10-01T09:00:00+08:00")
    finished = save_with_created_at(db_path, "2026-10-01T10:00:00+08:00")
    upcoming = save_with_created_at(db_path, "2026-10-01T11:00:00+08:00")
    update_inquiry_follow_up(
        overdue, status="待跟进", priority="中", notes="",
        next_follow_up_date=(today - timedelta(days=1)).isoformat(), db_path=db_path,
    )
    update_inquiry_follow_up(
        finished, status="已完成", priority="中", notes="",
        next_follow_up_date=(today - timedelta(days=5)).isoformat(), db_path=db_path,
    )
    update_inquiry_follow_up(
        upcoming, status="跟进中", priority="中", notes="",
        next_follow_up_date=(today + timedelta(days=3)).isoformat(), db_path=db_path,
    )

    assert count_overdue_follow_ups(today, db_path) == 1

    with pytest.raises(InquiryHistoryError, match="开始日期"):
        get_inquiry_analysis_summary("2026-10-10", "2026-10-01", db_path)
    with pytest.raises(InquiryHistoryError, match="格式无效"):
        list_inquiry_records(db_path, start_date="not-a-date")


def test_status_filter_applies_to_every_statistic(tmp_path) -> None:
    db_path = tmp_path / "scope.sqlite3"
    save_with_created_at(db_path, "2026-10-09T09:00:00+08:00")
    in_progress = save_with_created_at(db_path, "2026-10-10T09:00:00+08:00")
    completed = save_with_created_at(db_path, "2026-10-11T09:00:00+08:00")
    update_inquiry_follow_up(
        in_progress, status="跟进中", priority="高", notes="",
        next_follow_up_date=None, db_path=db_path,
    )
    update_inquiry_follow_up(
        completed, status="已完成", priority="低", notes="",
        next_follow_up_date=None, db_path=db_path,
    )

    scoped = get_inquiry_analysis_summary(status="跟进中", db_path=db_path)
    assert (scoped.total, scoped.pending, scoped.in_progress) == (1, 0, 1)
    assert (scoped.completed, scoped.processed) == (0, 1)
    assert scoped.draft_generated == 1
    assert get_inquiry_status_distribution(status="跟进中", db_path=db_path) == [
        ("跟进中", 1)
    ]
    assert get_inquiry_trend("day", db_path=db_path, status="已完成") == [
        ("2026-10-11", 1)
    ]
    assert get_inquiry_product_distribution(status="待跟进", db_path=db_path)[1] == 1
    assert get_inquiry_country_distribution(status="待跟进", db_path=db_path)[2] == 1
    assert get_inquiry_missing_field_stats(status="跟进中", db_path=db_path)
    assert [
        record.record_id
        for record in list_inquiry_records(db_path, status="已完成")
    ] == [completed]

    # 状态筛选与日期范围叠加生效，边界日期包含在内。
    same_day = get_inquiry_analysis_summary(
        "2026-10-11", "2026-10-11", status="已完成", db_path=db_path
    )
    assert same_day.total == 1
    assert get_inquiry_analysis_summary(
        "2026-10-11", "2026-10-11", status="待跟进", db_path=db_path
    ).total == 0

    with pytest.raises(InquiryHistoryError, match="状态"):
        get_inquiry_analysis_summary(status="未定义状态", db_path=db_path)


def test_previous_period_and_recent_window_are_rule_based(tmp_path) -> None:
    db_path = tmp_path / "period.sqlite3"
    for created_at in (
        "2026-10-01T09:00:00+08:00",
        "2026-10-09T09:00:00+08:00",
        "2026-10-10T09:00:00+08:00",
        "2026-10-11T09:00:00+08:00",
        "2026-10-12T09:00:00+08:00",
        "2026-10-13T09:00:00+08:00",
    ):
        save_with_created_at(db_path, created_at)

    # 未限定日期范围时不做环比，避免与历史数据比较得出误导结论。
    assert get_inquiry_analysis_summary(db_path=db_path).previous_total is None

    window = get_inquiry_analysis_summary("2026-10-11", "2026-10-13", db_path)
    assert window.total == 3
    # 等长紧邻前一期为 10-08 至 10-10，共 2 条。
    assert window.previous_total == 2
    assert window.recent_window_days == RECENT_WINDOW_DAYS
    # 近期新增只统计筛选范围自身最后 7 个自然日（10-07 至 10-13）内的记录，
    # 不会把范围外的历史记录算进来，因此本次为 3 条。
    assert window.recent_new == 3

    insights = build_business_insights(window)
    assert any(
        "询盘数量增加" in item and "变化 1 条" in item for item in insights
    )
    assert any("仅描述数据分布，不代表因果关系" in item for item in insights)

    small = get_inquiry_analysis_summary("2026-10-12", "2026-10-13", db_path)
    small_insights = build_business_insights(small)
    assert any("样本量不足" in item for item in small_insights)


def test_csv_export_respects_status_filter_and_empty_records(tmp_path) -> None:
    db_path = tmp_path / "export-filter.sqlite3"
    pending = save_with_created_at(
        db_path,
        "2026-10-10T09:00:00+08:00",
        inquiry="We are a furniture importer based in Germany. Please quote tables.",
    )
    completed = save_with_created_at(db_path, "2026-10-11T09:00:00+08:00")
    update_inquiry_follow_up(
        completed, status="已完成", priority="低", notes="",
        next_follow_up_date=None, db_path=db_path,
    )

    rows = export_inquiry_records_csv(status="已完成", db_path=db_path).splitlines()
    assert len(rows) == 2
    assert rows[0].lstrip("\ufeff") == ",".join(CSV_EXPORT_HEADERS)
    assert completed in rows[1]
    assert pending not in rows[1]

    # 没有匹配记录时只返回表头，不报错也不产生空行。
    empty = export_inquiry_records_csv(
        status="待跟进", start_date="2026-01-01", end_date="2026-01-02",
        db_path=db_path,
    )
    assert empty.splitlines() == ["\ufeff" + ",".join(CSV_EXPORT_HEADERS)]

    # 页面复用已读取的记录生成 CSV，结果与数据层直接导出一致。
    records = list_inquiry_records(db_path, status="已完成")
    assert build_inquiry_records_csv(records) == export_inquiry_records_csv(
        status="已完成", db_path=db_path
    )
    assert build_inquiry_records_csv([]) == (
        "\ufeff" + ",".join(CSV_EXPORT_HEADERS) + "\n"
    )
