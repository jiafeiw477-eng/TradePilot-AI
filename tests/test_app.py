from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import services.inquiry_history as inquiry_history
import services.inquiry_ai as inquiry_ai
import services.deepseek_client as deepseek_client
from services.inquiry_ai import InquiryAIAnalysis


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
PAGE_NAMES = [
    "业务概览",
    "询盘助手",
    "产品目录",
    "客户管理",
    "设置",
    "历史记录",
    "询盘管理",
]


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
    assert list(app_without_api.radio[0].options) == PAGE_NAMES
    assert app_without_api.header[0].value == "业务概览"


@pytest.mark.parametrize(
    "page_name",
    ["业务概览", "产品目录", "客户管理", "设置"],
)
def test_other_modules_keep_their_placeholder(page_name: str) -> None:
    app = AppTest.from_file(str(APP_PATH)).run()
    app.radio[0].set_value(page_name).run()

    assert app.header[0].value == page_name
    assert any("占位页面" in item.value for item in app.info)


def test_inquiry_page_shows_real_ai_disclosure_and_example(
    app_without_api,
) -> None:
    app_without_api.radio[0].set_value("询盘助手").run()

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
    app.radio[0].set_value("询盘助手").run()

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
    app_without_api.radio[0].set_value("询盘助手").run()
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
    app.radio[0].set_value("询盘助手").run()
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
    app.radio[0].set_value("询盘助手").run()
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
    app.radio[0].set_value("历史记录").run()

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
    app.radio[0].set_value("历史记录").run()

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
    app.radio[0].set_value("询盘管理").run()

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
    app.radio[0].set_value("询盘管理").run()

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
    app.radio[0].set_value("询盘管理").run()
    app.text_input[0].set_value("no matching customer").run()

    assert not app.exception
    assert any("没有符合当前关键词" in item.value for item in app.info)
