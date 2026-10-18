from __future__ import annotations

from types import SimpleNamespace

import pytest

import services.ai_assistant as ai_assistant
import services.app_settings as app_settings
import services.customer_manager as customer_manager
import services.deepseek_client as deepseek_client
import services.inquiry_history as inquiry_history
import services.product_catalog as product_catalog
from services.inquiry_ai import InquiryAIAnalysis


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


def test_context_is_empty_when_database_has_no_matching_records(tmp_path) -> None:
    db_path = tmp_path / "empty.sqlite3"

    context = ai_assistant.build_business_context("请介绍大理石茶几", db_path=db_path)

    assert context.has_records is False
    assert context.summary() == ""
    prompt = ai_assistant.build_context_prompt(context)
    assert "数据库中未找到匹配记录" in prompt
    assert "不得编造" in prompt


def test_context_uses_real_product_customer_and_inquiry_records(tmp_path) -> None:
    db_path = tmp_path / "shared.sqlite3"
    product_catalog.create_product(
        "橡木餐桌",
        sku="TBL-001",
        category="家具",
        price="180.5",
        currency="USD",
        moq="50",
        status="在售",
        db_path=db_path,
    )
    customer_id = customer_manager.create_customer(
        "Möbel GmbH",
        contact_name="Anna",
        country="Germany",
        level="重点客户",
        db_path=db_path,
    )
    record_id = inquiry_history.save_inquiry_record(
        "Please quote 200 dining tables.",
        sample_analysis(),
        db_path,
        customer_id=customer_id,
    )

    context = ai_assistant.build_business_context(
        "请介绍橡木餐桌，并结合 Möbel GmbH 的询盘给出跟进建议",
        inquiry_record_id=record_id,
        db_path=db_path,
    )

    assert [item.name for item in context.products] == ["橡木餐桌"]
    assert [item.company_name for item in context.customers] == ["Möbel GmbH"]
    assert [item.record_id for item in context.inquiries] == [record_id]
    assert context.summary() == "产品 1 条、客户 1 条、询盘 1 条"

    prompt = ai_assistant.build_context_prompt(context)
    assert "USD 180.50" in prompt
    assert "MOQ：50" in prompt
    assert "公司：Möbel GmbH" in prompt
    assert "Please quote 200 dining tables." in prompt


def test_explicit_customer_and_inquiry_take_priority_in_context(tmp_path) -> None:
    db_path = tmp_path / "shared.sqlite3"
    customer_id = customer_manager.create_customer("目标客户", db_path=db_path)
    record_id = inquiry_history.save_inquiry_record(
        "Please quote 200 dining tables.",
        sample_analysis(),
        db_path,
        customer_id=customer_id,
    )

    context = ai_assistant.build_business_context(
        "请给出下一步建议",
        customer_id=customer_id,
        inquiry_record_id=record_id,
        db_path=db_path,
    )

    assert [item.company_name for item in context.customers] == ["目标客户"]
    assert [item.record_id for item in context.inquiries] == [record_id]


def test_build_messages_includes_rules_preferences_and_trimmed_history(tmp_path) -> None:
    db_path = tmp_path / "settings.sqlite3"
    settings = app_settings.save_settings(
        {"reply_language": "英语", "reply_tone": "正式"}, db_path
    )
    history = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": f"第 {index} 条"}
        for index in range(ai_assistant.MAX_HISTORY_MESSAGES + 4)
    ]

    messages, context = ai_assistant.build_messages(
        history, "请分析这条询盘", settings=settings, db_path=db_path
    )

    assert messages[0]["role"] == "system"
    assert "不得编造" in messages[0]["content"]
    assert "回复语言" in messages[0]["content"]
    assert "真实业务数据" in messages[0]["content"]
    # 系统提示 + 裁剪后的历史 + 当前用户消息。
    assert len(messages) == 1 + ai_assistant.MAX_HISTORY_MESSAGES + 1
    assert messages[-1] == {"role": "user", "content": "请分析这条询盘"}
    assert context.has_records is False


def test_trim_history_keeps_only_recent_messages() -> None:
    messages = [{"role": "user", "content": str(index)} for index in range(20)]

    trimmed = ai_assistant.trim_history(messages)

    assert len(trimmed) == ai_assistant.MAX_HISTORY_MESSAGES
    assert trimmed[-1]["content"] == "19"
    assert ai_assistant.trim_history([]) == []


def test_generate_reply_rejects_blank_and_overlong_input(tmp_path) -> None:
    with pytest.raises(ai_assistant.AIAssistantError, match="请先输入要发送的内容"):
        ai_assistant.generate_reply([], "   ", db_path=tmp_path / "s.sqlite3")
    with pytest.raises(ai_assistant.AIAssistantError, match="单条消息过长"):
        ai_assistant.generate_reply(
            [], "x" * (ai_assistant.MAX_MESSAGE_LENGTH + 1), db_path=tmp_path / "s.sqlite3"
        )


def test_generate_reply_reports_missing_configuration(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        deepseek_client,
        "load_settings",
        lambda: (_ for _ in ()).throw(
            deepseek_client.DeepSeekConfigurationError("DeepSeek API 密钥未设置。")
        ),
    )

    with pytest.raises(ai_assistant.AIAssistantError, match="密钥未设置"):
        ai_assistant.generate_reply([], "你好", db_path=tmp_path / "s.sqlite3")


def test_generate_reply_stops_when_business_data_cannot_be_read(
    monkeypatch, tmp_path
) -> None:
    fake_client = _install_fake_client(monkeypatch, "回答")
    monkeypatch.setattr(
        product_catalog,
        "find_products_for_query",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            product_catalog.ProductError("无法初始化产品目录数据库。")
        ),
    )
    db_path = tmp_path / "shared.sqlite3"

    context = ai_assistant.build_business_context("请介绍产品", db_path=db_path)
    assert context.errors == ["产品目录读取失败：无法初始化产品目录数据库。"]
    context_prompt = ai_assistant.build_context_prompt(context)
    assert "不能据此判断数据库中没有匹配记录" in context_prompt
    assert "数据库中未找到匹配记录" not in context_prompt

    with pytest.raises(ai_assistant.AIAssistantError, match="已取消 DeepSeek 请求"):
        ai_assistant.generate_reply([], "请介绍产品", db_path=db_path)

    assert fake_client.captured == {}


class _FakeClient:
    """记录调用参数并返回固定内容的最小 DeepSeek 客户端替身。"""

    def __init__(self, content: str | None) -> None:
        self.captured: dict[str, object] = {}
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )
        self._content = content

    def _create(self, **kwargs):
        self.captured = kwargs
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self._content),
                    finish_reason="stop",
                )
            ]
        )


def _install_fake_client(monkeypatch, content: str | None) -> _FakeClient:
    fake_client = _FakeClient(content)
    monkeypatch.setattr(
        deepseek_client,
        "load_settings",
        lambda: deepseek_client.DeepSeekSettings(
            api_key="sk-test-key",
            base_url="https://api.example.com/v1",
            model="deepseek-chat",
        ),
    )
    monkeypatch.setattr(
        deepseek_client,
        "create_client",
        lambda settings, **kwargs: fake_client,
    )
    return fake_client


def test_generate_reply_returns_content_and_used_context(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "shared.sqlite3"
    product_catalog.create_product("橡木餐桌", sku="TBL-001", price="180.5", db_path=db_path)
    app_settings.save_settings({"reply_language": "英语"}, db_path)
    fake_client = _install_fake_client(monkeypatch, "Dear Anna, thank you for your inquiry.")

    reply, context = ai_assistant.generate_reply(
        [], "请介绍橡木餐桌", db_path=db_path
    )

    assert reply == "Dear Anna, thank you for your inquiry."
    assert [item.name for item in context.products] == ["橡木餐桌"]
    assert context.summary() == "产品 1 条"
    # 必须复用现有 DeepSeek 调用方式，并禁用 thinking 以保证响应速度。
    assert fake_client.captured["model"] == "deepseek-chat"
    assert fake_client.captured["extra_body"] == {"thinking": {"type": "disabled"}}
    assert fake_client.captured["max_tokens"] == ai_assistant.MAX_OUTPUT_TOKENS
    sent_messages = fake_client.captured["messages"]
    assert sent_messages[-1]["content"] == "请介绍橡木餐桌"


def test_generate_reply_reports_empty_api_result(monkeypatch, tmp_path) -> None:
    _install_fake_client(monkeypatch, "   ")

    with pytest.raises(ai_assistant.AIAssistantError, match="空结果"):
        ai_assistant.generate_reply([], "你好", db_path=tmp_path / "s.sqlite3")


def test_generate_reply_reports_api_status_error(monkeypatch, tmp_path) -> None:
    class _FailingClient(_FakeClient):
        def _create(self, **kwargs):
            from httpx import Request, Response

            from openai import APIStatusError

            request = Request("POST", "https://api.example.com/v1/chat/completions")
            response = Response(401, request=request, json={"error": {"code": "invalid_api_key"}})
            raise APIStatusError(
                "Unauthorized", response=response, body={"error": {"code": "invalid_api_key"}}
            )

    failing_client = _FailingClient(None)
    monkeypatch.setattr(
        deepseek_client,
        "load_settings",
        lambda: deepseek_client.DeepSeekSettings(
            api_key="sk-test-key",
            base_url="https://api.example.com/v1",
            model="deepseek-chat",
        ),
    )
    monkeypatch.setattr(
        deepseek_client, "create_client", lambda settings, **kwargs: failing_client
    )

    with pytest.raises(ai_assistant.AIAssistantError) as excinfo:
        ai_assistant.generate_reply([], "你好", db_path=tmp_path / "s.sqlite3")

    message = str(excinfo.value)
    assert "HTTP 401" in message
    assert "invalid_api_key" in message
    # 错误信息不得回显真实密钥内容。
    assert "sk-test-key" not in message
