from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError
from httpx import Request, Response

import services.inquiry_ai as inquiry_ai
from services.deepseek_client import DeepSeekConfigurationError, DeepSeekSettings


def valid_response() -> dict:
    return {
        "translation_zh": "我们需要 200 张餐桌。",
        "customer_needs": {
            "product": "餐桌",
            "specifications": "未提供",
            "quantity": "200 张",
            "target_price": "未提供",
            "delivery_time": "未提供",
            "trade_terms": "未提供",
            "other_requirements": "未提供",
        },
        "intent_level": "中",
        "intent_rationale": "客户提供了数量；此判断仅供参考。",
        "risks_and_questions": ["请确认规格。"],
        "reply_draft_en": "Dear Customer, thank you.",
        "reply_draft_zh": "尊敬的客户，谢谢。",
        "follow_up_suggestions": ["确认产品规格。"],
    }


class FakeChatCompletions:
    def __init__(
        self,
        content: str | None,
        *,
        finish_reason: str | None = "stop",
        reasoning_content: str | None = None,
        exception: Exception | None = None,
        has_choices: bool = True,
    ):
        self.content = content
        self.finish_reason = finish_reason
        self.reasoning_content = reasoning_content
        self.exception = exception
        self.has_choices = has_choices
        self.request = None

    def create(self, **kwargs):
        self.request = kwargs
        if self.exception:
            raise self.exception
        message = SimpleNamespace(
            content=self.content,
            reasoning_content=self.reasoning_content,
        )
        choices = (
            [SimpleNamespace(message=message, finish_reason=self.finish_reason)]
            if self.has_choices
            else []
        )
        return SimpleNamespace(
            choices=choices,
        )


class FakeClient:
    def __init__(
        self,
        content: str | None,
        *,
        finish_reason: str | None = "stop",
        reasoning_content: str | None = None,
        exception: Exception | None = None,
        has_choices: bool = True,
    ):
        self.completions = FakeChatCompletions(
            content,
            finish_reason=finish_reason,
            reasoning_content=reasoning_content,
            exception=exception,
            has_choices=has_choices,
        )
        self.chat = SimpleNamespace(completions=self.completions)


def configure_fake_client(
    monkeypatch,
    content: str | None,
    *,
    finish_reason: str | None = "stop",
    reasoning_content: str | None = None,
    exception: Exception | None = None,
    has_choices: bool = True,
) -> FakeClient:
    client = FakeClient(
        content,
        finish_reason=finish_reason,
        reasoning_content=reasoning_content,
        exception=exception,
        has_choices=has_choices,
    )
    monkeypatch.setattr(
        inquiry_ai,
        "load_settings",
        lambda: DeepSeekSettings(
            api_key="test-secret",
            base_url="https://example.invalid/v1",
            model="test-model",
        ),
    )

    def fake_create_client(settings, **kwargs):
        client.client_options = kwargs
        return client

    monkeypatch.setattr(
        inquiry_ai,
        "create_client",
        fake_create_client,
    )
    return client


def test_ai_analysis_uses_configured_model_and_returns_structured_result(
    monkeypatch,
) -> None:
    client = configure_fake_client(monkeypatch, json.dumps(valid_response()))

    result = inquiry_ai.analyze_inquiry_with_ai("Please quote 200 dining tables.")

    assert result.translation_zh == "我们需要 200 张餐桌。"
    assert result.customer_needs["target_price"] == "未提供"
    assert result.intent_level == "中"
    assert result.reply_draft_en == "Dear Customer, thank you."
    assert client.completions.request["model"] == "test-model"
    assert client.completions.request["response_format"] == {"type": "json_object"}
    assert client.completions.request["extra_body"] == {
        "thinking": {"type": "disabled"}
    }
    assert client.completions.request["max_tokens"] == inquiry_ai.MAX_OUTPUT_TOKENS
    assert client.completions.request["timeout"] == 90.0
    assert client.client_options == {"timeout": 90.0, "max_retries": 0}
    assert client.completions.request["messages"][1]["content"] == (
        "Please quote 200 dining tables."
    )


@pytest.mark.parametrize("inquiry", ["", "  "])
def test_empty_input_is_rejected_before_client_initialization(inquiry) -> None:
    with pytest.raises(inquiry_ai.InquiryAIError, match="请先输入"):
        inquiry_ai.analyze_inquiry_with_ai(inquiry)


def test_missing_configuration_becomes_user_friendly_error(monkeypatch) -> None:
    monkeypatch.setattr(
        inquiry_ai,
        "load_settings",
        lambda: (_ for _ in ()).throw(
            DeepSeekConfigurationError("请检查 .env 中的 DeepSeek API 配置。")
        ),
    )

    with pytest.raises(inquiry_ai.InquiryAIError, match="请检查 .env"):
        inquiry_ai.analyze_inquiry_with_ai("Hello, please send a quotation.")


def test_invalid_json_response_is_rejected_without_fabricating_result(
    monkeypatch,
) -> None:
    configure_fake_client(monkeypatch, "not json")

    with pytest.raises(inquiry_ai.InquiryAIError, match="格式无效"):
        inquiry_ai.analyze_inquiry_with_ai("Hello, please send a quotation.")


def test_null_content_logs_safe_response_diagnostics(caplog) -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=None, reasoning_content=None),
                finish_reason="length",
            )
        ],
        usage=SimpleNamespace(completion_tokens=812),
    )

    assert inquiry_ai._log_empty_response(response, "test-model") == (
        "length",
        False,
    )
    assert "choices_count=1" in caplog.text
    assert "finish_reason=length" in caplog.text
    assert "completion_tokens=812" in caplog.text
    assert "model=test-model" in caplog.text


def test_empty_final_content_reports_token_limit_and_logs_reasoning(
    monkeypatch, caplog
) -> None:
    configure_fake_client(
        monkeypatch,
        None,
        finish_reason="length",
        reasoning_content="private reasoning text",
    )

    with caplog.at_level(logging.WARNING, logger=inquiry_ai.__name__):
        with pytest.raises(inquiry_ai.InquiryAIError, match="Token 上限"):
            inquiry_ai.analyze_inquiry_with_ai("Please analyze this inquiry.")

    assert "finish_reason=length" in caplog.text
    assert "reasoning_content_present=True" in caplog.text
    assert "private reasoning text" not in caplog.text


def test_empty_final_content_with_reasoning_is_not_displayed_as_answer(
    monkeypatch,
) -> None:
    configure_fake_client(
        monkeypatch,
        "",
        finish_reason="stop",
        reasoning_content="private reasoning text",
    )

    with pytest.raises(inquiry_ai.InquiryAIError, match="思考过程但没有最终答复"):
        inquiry_ai.analyze_inquiry_with_ai("Please analyze this inquiry.")


def test_response_without_choices_reports_empty_response_and_logs_diagnostics(
    monkeypatch, caplog
) -> None:
    configure_fake_client(monkeypatch, None, has_choices=False)

    with caplog.at_level(logging.WARNING, logger=inquiry_ai.__name__):
        with pytest.raises(inquiry_ai.InquiryAIError, match="空结果"):
            inquiry_ai.analyze_inquiry_with_ai("Please analyze this inquiry.")

    assert "has_choices=False" in caplog.text
    assert "choices_count=0" in caplog.text


def test_http_error_diagnostics_redact_api_key(monkeypatch, caplog) -> None:
    api_key = "test-secret-api-key"
    response = Response(
        401,
        request=Request("POST", "https://example.invalid/chat/completions"),
    )
    error = APIStatusError(
        "Unauthorized",
        response=response,
        body={
            "error": {
                "code": "invalid_api_key",
                "message": f"Rejected credential {api_key}",
            }
        },
    )
    configure_fake_client(monkeypatch, None, exception=error)

    with caplog.at_level(logging.ERROR, logger=inquiry_ai.__name__):
        with pytest.raises(
            inquiry_ai.InquiryAIError,
            match="HTTP 401",
        ) as raised:
            inquiry_ai.analyze_inquiry_with_ai("Please analyze this inquiry.")

    assert "APIStatusError" in str(raised.value)
    assert "invalid_api_key" in str(raised.value)
    assert "Rejected credential [已隐藏]" in str(raised.value)
    assert api_key not in str(raised.value)
    assert "http_status=401" in caplog.text
    assert "error_code=invalid_api_key" in caplog.text
    assert api_key not in caplog.text
    assert "Rejected credential [已隐藏]" in caplog.text


def test_timeout_is_reported_separately_without_retry(monkeypatch, caplog) -> None:
    error = APITimeoutError(
        request=Request("POST", "https://example.invalid/chat/completions")
    )
    client = configure_fake_client(monkeypatch, None, exception=error)

    with caplog.at_level(logging.ERROR, logger=inquiry_ai.__name__):
        with pytest.raises(inquiry_ai.InquiryAIError, match="请求超时") as raised:
            inquiry_ai.analyze_inquiry_with_ai("Please analyze this inquiry.")

    assert "90 秒" in str(raised.value)
    assert "APITimeoutError" in str(raised.value)
    assert "未自动重试" in str(raised.value)
    assert "无法连接 DeepSeek API" not in str(raised.value)
    assert client.client_options["max_retries"] == 0
    assert "timeout_seconds=90.0" in caplog.text


def test_connection_error_is_not_reported_as_timeout(monkeypatch) -> None:
    error = APIConnectionError(
        message="Connection refused",
        request=Request("POST", "https://example.invalid/chat/completions"),
    )
    configure_fake_client(monkeypatch, None, exception=error)

    with pytest.raises(
        inquiry_ai.InquiryAIError,
        match="网络连接失败",
    ) as raised:
        inquiry_ai.analyze_inquiry_with_ai("Please analyze this inquiry.")

    assert "请求超时" not in str(raised.value)
    assert "APIConnectionError" in str(raised.value)
