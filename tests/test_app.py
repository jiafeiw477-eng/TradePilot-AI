from datetime import date, timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import services.ai_assistant as ai_assistant
import services.app_settings as app_settings
import services.customer_manager as customer_manager
import services.inquiry_history as inquiry_history
import services.inquiry_ai as inquiry_ai
import services.deepseek_client as deepseek_client
import services.product_catalog as product_catalog
from services.inquiry_ai import InquiryAIAnalysis


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
PAGE_NAMES = [
    "业务概览",
    "询盘助手",
    "AI 助手",
    "产品目录",
    "客户管理",
    "历史记录",
    "询盘管理",
    "设置",
]


def nav_radio(app: AppTest):
    """定位侧边栏导航控件。

    业务概览页自身也包含 radio（趋势统计粒度），因此不能用固定下标取导航。
    """
    return next(item for item in app.radio if list(item.options) == PAGE_NAMES)


def goto_page(app: AppTest, page_name: str) -> AppTest:
    nav_radio(app).set_value(page_name)
    return app.run()


def make_sample_analysis() -> InquiryAIAnalysis:
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


@pytest.fixture
def app_without_api(monkeypatch):
    monkeypatch.setattr(
        inquiry_ai,
        "analyze_inquiry_with_ai",
        lambda inquiry: pytest.fail("页面初次加载不应调用 DeepSeek API"),
    )
    return AppTest.from_file(str(APP_PATH)).run()


def test_all_navigation_modules_are_available(app_without_api) -> None:
    assert list(nav_radio(app_without_api).options) == PAGE_NAMES
    assert app_without_api.header[0].value == "业务概览"


@pytest.mark.parametrize(
    "page_name",
    ["AI 助手", "产品目录", "客户管理", "设置"],
)
def test_new_modules_render_without_placeholder(
    monkeypatch, tmp_path, page_name: str
) -> None:
    """四个新模块必须渲染真实页面，不能再显示占位提示。"""
    monkeypatch.setattr(
        inquiry_history, "DATABASE_PATH", tmp_path / "modules.sqlite3"
    )
    monkeypatch.setattr(
        inquiry_ai,
        "analyze_inquiry_with_ai",
        lambda inquiry: pytest.fail("页面初次加载不应调用 DeepSeek API"),
    )

    app = AppTest.from_file(str(APP_PATH)).run()
    goto_page(app, page_name)

    assert not app.exception
    assert app.header[0].value == page_name
    assert not any("占位页面" in item.value for item in app.info)


def test_inquiry_page_shows_real_ai_disclosure_and_example(
    app_without_api,
) -> None:
    goto_page(app_without_api, "询盘助手")

    assert not app_without_api.exception
    assert any("发送给 DeepSeek API" in item.value for item in app_without_api.warning)
    assert len(app_without_api.text_area) == 1
    assert "Germany" in app_without_api.text_area[0].value
    assert app_without_api.button[0].label == "AI 分析"


def test_inquiry_page_explains_missing_deepseek_configuration(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        deepseek_client,
        "load_settings",
        lambda: (_ for _ in ()).throw(
            deepseek_client.DeepSeekConfigurationError(
                "DeepSeek API 密钥未设置。"
            )
        ),
    )
    app = AppTest.from_file(str(APP_PATH)).run()
    goto_page(app, "询盘助手")

    assert not app.exception
    assert any(
        "Streamlit Community Cloud" in item.value
        for item in app.warning
    )
    assert any(
        "应用仍可浏览" in item.value
        for item in app.warning
    )


def test_empty_inquiry_is_rejected_without_api_call(app_without_api) -> None:
    goto_page(app_without_api, "询盘助手")
    app_without_api.text_area[0].set_value("   ")
    app_without_api.button[0].click().run()

    assert not app_without_api.exception
    assert any("请先输入英文询盘" in item.value for item in app_without_api.warning)


def test_unexpected_analysis_error_is_shown_and_not_saved(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        inquiry_history,
        "DATABASE_PATH",
        tmp_path / "history.sqlite3",
    )
    monkeypatch.setattr(
        inquiry_ai,
        "analyze_inquiry_with_ai",
        lambda inquiry: (_ for _ in ()).throw(RuntimeError("secret-like detail")),
    )
    app = AppTest.from_file(str(APP_PATH)).run()
    goto_page(app, "询盘助手")
    app.button[0].click().run()

    assert not app.exception
    assert any("RuntimeError" in item.value for item in app.error)
    assert all("secret-like detail" not in item.value for item in app.error)
    assert inquiry_history.list_inquiry_records() == []


def test_analysis_results_are_rendered_and_saved_when_user_submits(
    monkeypatch, tmp_path
) -> None:
    from services.inquiry_ai import InquiryAIAnalysis

    calls = []

    def fake_analyze(inquiry: str) -> InquiryAIAnalysis:
        calls.append(inquiry)
        return InquiryAIAnalysis(
            translation_zh="我们需要餐桌。",
            customer_needs={
                "product": "餐桌",
                "specifications": "未提供",
                "quantity": "200张",
                "target_price": "未提供",
                "delivery_time": "未提供",
                "trade_terms": "未提供",
                "other_requirements": "未提供",
            },
            intent_level="中",
            intent_rationale="询问产品和数量；此判断仅供参考。",
            risks_and_questions=["请确认规格。"],
            reply_draft_en="Dear Customer, thank you for your inquiry.",
            reply_draft_zh="尊敬的客户，感谢您的询盘。",
            follow_up_suggestions=["确认餐桌规格。"],
        )

    monkeypatch.setattr(inquiry_ai, "analyze_inquiry_with_ai", fake_analyze)
    monkeypatch.setattr(
        inquiry_history,
        "DATABASE_PATH",
        tmp_path / "history.sqlite3",
    )
    app = AppTest.from_file(str(APP_PATH)).run()
    goto_page(app, "询盘助手")
    app.button[0].click().run()

    assert not app.exception
    assert len(calls) == 1
    assert "Germany" in calls[0]
    rendered_text = "\n".join(item.value for item in app.markdown)
    assert "我们需要餐桌" in rendered_text
    assert "客户需求分析" in [item.value for item in app.subheader]
    assert "中意向" in rendered_text
    assert "请确认规格" in rendered_text
    assert "后续跟进建议" in [item.value for item in app.subheader]
    assert app.code[0].value == "Dear Customer, thank you for your inquiry."
    assert any("已保存到历史记录" in item.value for item in app.success)
    saved_records = inquiry_history.list_inquiry_records()
    assert len(saved_records) == 1
    assert saved_records[0].original_inquiry == calls[0]

    app.run()
    assert len(calls) == 1
    assert len(inquiry_history.list_inquiry_records()) == 1


def test_history_page_shows_empty_state_without_api_call(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        inquiry_history,
        "DATABASE_PATH",
        tmp_path / "empty-history.sqlite3",
    )
    app = AppTest.from_file(str(APP_PATH)).run()
    goto_page(app, "历史记录")

    assert not app.exception
    assert any("暂无历史询盘记录" in item.value for item in app.info)
    assert not app.button or all(item.label != "AI 分析" for item in app.button)


def test_history_page_displays_copyable_reply_and_requires_delete_confirmation(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        inquiry_history,
        "DATABASE_PATH",
        tmp_path / "history.sqlite3",
    )
    inquiry_history.save_inquiry_record(
        "Please quote 200 dining tables.", make_sample_analysis()
    )

    app = AppTest.from_file(str(APP_PATH)).run()
    goto_page(app, "历史记录")

    assert not app.exception
    assert app.code[0].value == "Dear Customer, thank you for your inquiry."
    delete_button = next(
        item for item in app.button if item.label == "删除此记录"
    )
    assert delete_button.disabled

    app.checkbox[0].set_value(True).run()
    delete_button = next(item for item in app.button if item.label == "删除此记录")
    delete_button.click().run()

    assert not app.exception
    assert any("暂无历史询盘记录" in item.value for item in app.info)


def test_management_page_shows_statistics_and_does_not_call_ai(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        inquiry_history,
        "DATABASE_PATH",
        tmp_path / "history.sqlite3",
    )
    inquiry_history.save_inquiry_record(
        "Please quote 200 dining tables.", make_sample_analysis()
    )
    monkeypatch.setattr(
        inquiry_ai,
        "analyze_inquiry_with_ai",
        lambda inquiry: pytest.fail("询盘管理页面不应调用 DeepSeek API"),
    )

    app = AppTest.from_file(str(APP_PATH)).run()
    goto_page(app, "询盘管理")

    assert not app.exception
    assert [metric.label for metric in app.metric] == [
        "询盘总数",
        "待跟进",
        "跟进中",
        "已完成",
        "高优先级",
    ]
    assert [metric.value for metric in app.metric] == ["1", "1", "0", "0", "0"]


def test_management_page_saves_edits_to_sqlite_without_api_call(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        inquiry_history,
        "DATABASE_PATH",
        tmp_path / "management.sqlite3",
    )
    record_id = inquiry_history.save_inquiry_record(
        "Please quote 200 dining tables.", make_sample_analysis()
    )
    monkeypatch.setattr(
        inquiry_ai,
        "analyze_inquiry_with_ai",
        lambda inquiry: pytest.fail("管理表单保存不应调用 DeepSeek API"),
    )
    app = AppTest.from_file(str(APP_PATH)).run()
    goto_page(app, "询盘管理")

    status_widget = next(
        item for item in app.selectbox if item.key == f"follow_up_status_{record_id}"
    )
    status_widget.set_value("跟进中")
    app.run()
    priority_widget = next(
        item for item in app.selectbox if item.key == f"follow_up_priority_{record_id}"
    )
    priority_widget.set_value("高")
    app.run()
    notes_widget = next(
        item for item in app.text_area if item.key == f"follow_up_notes_{record_id}"
    )
    notes_widget.set_value("已发送报价，周五联系")
    app.run()
    date_widget = next(
        item
        for item in app.date_input
        if item.key == f"next_follow_up_date_{record_id}"
    )
    date_widget.set_value(date(2026, 10, 23))
    app.run()

    assert not app.exception
    saved_record = inquiry_history.list_inquiry_records()[0]
    assert saved_record.follow_up_status == "跟进中"
    assert saved_record.priority == "高"
    assert saved_record.follow_up_notes == "已发送报价，周五联系"
    assert saved_record.next_follow_up_date == "2026-10-23"
    assert [metric.value for metric in app.metric] == ["1", "0", "1", "0", "1"]
    assert any("已自动保存" in item.value for item in app.success)


def test_management_page_shows_no_results_message(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        inquiry_history,
        "DATABASE_PATH",
        tmp_path / "management.sqlite3",
    )
    inquiry_history.save_inquiry_record(
        "Please quote 200 dining tables.", make_sample_analysis()
    )
    app = AppTest.from_file(str(APP_PATH)).run()
    goto_page(app, "询盘管理")
    app.text_input[0].set_value("no matching customer").run()

    assert not app.exception
    assert any("没有符合当前关键词" in item.value for item in app.info)


# ---------------------------------------------------------------------------
# 业务数据分析（业务概览页）
# ---------------------------------------------------------------------------


def metric_values(app: AppTest) -> list[str]:
    return [item.value for item in app.metric]


def find_widget(app: AppTest, collection: str, key: str):
    return next(item for item in getattr(app, collection) if item.key == key)


def test_analytics_page_reports_real_metrics_and_unavailable_reply_stats(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        inquiry_history, "DATABASE_PATH", tmp_path / "analytics.sqlite3"
    )
    monkeypatch.setattr(
        inquiry_ai,
        "analyze_inquiry_with_ai",
        lambda inquiry: pytest.fail("业务概览页面不应调用 DeepSeek API"),
    )

    app = AppTest.from_file(str(APP_PATH)).run()

    assert not app.exception
    assert app.header[0].value == "业务概览"
    assert [item.label for item in app.metric] == [
        "询盘总数",
        "待处理",
        "处理中",
        "已完成",
        "已生成回复草稿",
        "处理率",
        "近 7 天新增",
    ]
    # 空库时所有计数为 0；处理率分母为 0 时应显示“暂无数据”而不是报错。
    # “已生成回复草稿”不能等同于“已回复客户”，因此页面不提供“已回复/回复率”指标。
    assert metric_values(app) == ["0", "0", "0", "0", "0", "暂无数据", "0"]
    assert any("没有询盘记录" in item.value for item in app.info)
    assert any("暂无数据来源" in item.value for item in app.caption)
    # 空数据时不渲染导出，避免出现没有来源的统计内容。
    assert [item.label for item in app.download_button] == []


def test_analytics_page_renders_sections_and_status_filter(
    monkeypatch, tmp_path
) -> None:
    db_path = tmp_path / "analytics.sqlite3"
    monkeypatch.setattr(inquiry_history, "DATABASE_PATH", db_path)
    monkeypatch.setattr(
        inquiry_ai,
        "analyze_inquiry_with_ai",
        lambda inquiry: pytest.fail("业务概览页面不应调用 DeepSeek API"),
    )
    inquiry_history.save_inquiry_record(
        "We are a furniture importer based in Germany. "
        "Please quote 200 wooden dining tables.",
        make_sample_analysis(),
        db_path,
    )

    app = AppTest.from_file(str(APP_PATH)).run()

    assert not app.exception
    # 该记录已生成英文回复草稿，但数据库没有“已实际发送”记录，因此只统计草稿数量。
    assert metric_values(app) == ["1", "1", "0", "0", "1", "0%", "1"]
    assert [item.value for item in app.subheader] == [
        "询盘数量趋势",
        "跟进状态分布",
        "产品需求与客户国家",
        "业务洞察",
        "询盘数据明细与导出",
    ]
    assert [item.label for item in app.download_button] == ["下载询盘记录 (.csv)"]
    # 国家只能来自邮件中的明确表述，并如实标注识别比例。
    assert any("条记录中有 1 条可识别" in item.value for item in app.caption)
    assert any("已识别" in item.value or "产品信息来自" in item.value for item in app.caption)
    # 三张表：状态分布、信息完整度、询盘明细。
    assert len(app.dataframe) == 3
    assert list(app.dataframe[2].value["产品"]) == ["餐桌"]

    # 切换趋势粒度重新聚合，不应报错或触发 AI 调用。
    find_widget(app, "radio", "analytics_trend_granularity").set_value("按周")
    app.run()
    assert not app.exception
    assert metric_values(app)[0] == "1"

    # 按跟进状态筛选后，指标、图表与明细表使用同一口径。
    find_widget(app, "selectbox", "analytics_status_filter").set_value("待跟进")
    app.run()
    assert not app.exception
    assert metric_values(app) == ["1", "1", "0", "0", "1", "0%", "1"]
    assert any("共 1 条记录" in item.value for item in app.caption)
    assert len(app.dataframe) == 3

    # 筛选到没有记录的状态时给出明确空状态，且不渲染导出按钮。
    find_widget(app, "selectbox", "analytics_status_filter").set_value("已完成")
    app.run()
    assert not app.exception
    assert metric_values(app) == ["0", "0", "0", "0", "0", "暂无数据", "0"]
    assert any("没有询盘记录" in item.value for item in app.info)
    assert [item.label for item in app.download_button] == []


def test_analytics_date_range_filter_affects_metrics(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "range.sqlite3"
    monkeypatch.setattr(inquiry_history, "DATABASE_PATH", db_path)
    monkeypatch.setattr(
        inquiry_ai,
        "analyze_inquiry_with_ai",
        lambda inquiry: pytest.fail("业务概览页面不应调用 DeepSeek API"),
    )
    inquiry_history.save_inquiry_record(
        "Please quote 200 wooden dining tables.",
        make_sample_analysis(),
        db_path,
    )
    app = AppTest.from_file(str(APP_PATH)).run()

    assert find_widget(app, "selectbox", "analytics_quick_range").value == "最近 30 天"
    assert metric_values(app)[0] == "1"

    for quick_range in ("最近 7 天", "全部时间"):
        find_widget(app, "selectbox", "analytics_quick_range").set_value(quick_range)
        app.run()
        assert not app.exception
        assert metric_values(app)[0] == "1"

    find_widget(app, "selectbox", "analytics_quick_range").set_value("自定义")
    app.run()
    assert not app.exception
    assert metric_values(app)[0] == "1"

    # 开始日期晚于结束日期时给出明确提示，且不再渲染统计内容。
    find_widget(app, "date_input", "analytics_end_date").set_value(
        date.today() - timedelta(days=1)
    )
    app.run()
    assert not app.exception
    assert any("不能晚于结束日期" in item.value for item in app.warning)
    assert [item.label for item in app.metric] == []


def test_analytics_page_rerun_does_not_write_or_call_ai(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "analytics.sqlite3"
    monkeypatch.setattr(inquiry_history, "DATABASE_PATH", db_path)
    monkeypatch.setattr(
        inquiry_ai,
        "analyze_inquiry_with_ai",
        lambda inquiry: pytest.fail("业务概览页面不应调用 DeepSeek API"),
    )
    inquiry_history.save_inquiry_record(
        "We are a furniture importer based in Germany. Please quote tables.",
        make_sample_analysis(),
        db_path,
    )
    before = len(inquiry_history.list_inquiry_records(db_path))

    app = AppTest.from_file(str(APP_PATH)).run()
    assert not app.exception
    assert [item.label for item in app.download_button] == ["下载询盘记录 (.csv)"]

    # 反复重跑页面（图表切换、导出按钮渲染）都不会写入数据库或重新调用 AI。
    for _ in range(3):
        app.run()
        assert not app.exception
    assert len(inquiry_history.list_inquiry_records(db_path)) == before

    export = inquiry_history.export_inquiry_records_csv(db_path=db_path)
    lowered = export.lower()
    assert export.startswith("\ufeff")
    assert "餐桌" in export
    # 导出内容只能是业务字段，不能带出密钥或内部配置。
    for forbidden in ("api_key", "deepseek_api", "sk-", ".env", "secret", "token"):
        assert forbidden not in lowered


def test_settings_page_reports_database_error_without_fallback(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        inquiry_history, "DATABASE_PATH", tmp_path / "settings.sqlite3"
    )
    monkeypatch.setattr(
        app_settings,
        "load_settings",
        lambda: (_ for _ in ()).throw(
            app_settings.SettingsError("读取系统设置失败。")
        ),
    )

    app = AppTest.from_file(str(APP_PATH)).run()
    goto_page(app, "设置")

    assert not app.exception
    assert any("读取设置失败：读取系统设置失败" in item.value for item in app.error)


def test_ai_assistant_clear_requires_confirmation(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        inquiry_history, "DATABASE_PATH", tmp_path / "assistant.sqlite3"
    )
    monkeypatch.setattr(
        deepseek_client,
        "load_settings",
        lambda: (_ for _ in ()).throw(
            deepseek_client.DeepSeekConfigurationError("尚未配置。")
        ),
    )
    app = AppTest.from_file(str(APP_PATH)).run()
    app.session_state["ai_assistant_messages"] = [
        {"role": "user", "content": "保留这条测试对话"},
        {"role": "assistant", "content": "测试回复"},
    ]
    goto_page(app, "AI 助手")

    clear_button = find_widget(app, "button", "ai_assistant_clear")
    assert clear_button.disabled
    assert len(app.session_state["ai_assistant_messages"]) == 2

    find_widget(app, "checkbox", "ai_assistant_clear_confirm").set_value(True)
    app.run()
    clear_button = find_widget(app, "button", "ai_assistant_clear")
    assert not clear_button.disabled

    clear_button.click().run()

    assert not app.exception
    assert app.session_state["ai_assistant_messages"] == []
    assert find_widget(app, "checkbox", "ai_assistant_clear_confirm").value is False
