from __future__ import annotations

import services.deepseek_client as deepseek_client


def test_load_settings_reads_environment_without_exposing_api_key(
    monkeypatch,
) -> None:
    monkeypatch.setattr(deepseek_client, "load_dotenv", lambda **kwargs: None)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "test-model")

    settings = deepseek_client.load_settings()

    assert settings.api_key == "test-secret"
    assert settings.base_url == "https://example.invalid/v1"
    assert settings.model == "test-model"
    assert "test-secret" not in repr(settings)


def test_missing_api_key_returns_clear_chinese_connection_result(
    monkeypatch,
) -> None:
    monkeypatch.setattr(deepseek_client, "load_dotenv", lambda **kwargs: None)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "test-model")

    result = deepseek_client.test_connection()

    assert not result.connected
    assert "API 密钥未设置" in result.message
    assert "DEEPSEEK_API_KEY" in result.message


def test_connection_test_uses_configured_model_without_real_request(
    monkeypatch,
) -> None:
    monkeypatch.setattr(deepseek_client, "load_dotenv", lambda **kwargs: None)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "test-model")
    calls = {}
    monkeypatch.setattr(
        deepseek_client,
        "load_settings",
        lambda: deepseek_client.DeepSeekSettings(
            api_key="test-secret",
            base_url="https://example.invalid/v1",
            model="test-model",
        ),
    )

    class FakeCompletions:
        def create(self, **kwargs):
            calls["request"] = kwargs
            return object()

    class FakeClient:
        def __init__(self, **kwargs):
            calls["client"] = kwargs
            self.chat = type(
                "FakeChat",
                (),
                {"completions": FakeCompletions()},
            )()

    monkeypatch.setattr(deepseek_client, "OpenAI", FakeClient)

    result = deepseek_client.test_connection()

    assert result.connected
    assert calls["client"]["api_key"] == "test-secret"
    assert calls["client"]["base_url"] == "https://example.invalid/v1"
    assert calls["request"]["model"] == "test-model"
    assert calls["request"]["max_tokens"] == 5
