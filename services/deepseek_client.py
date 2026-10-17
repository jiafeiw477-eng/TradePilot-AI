from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from openai import APIError, OpenAI
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"

REQUIRED_SETTINGS = {
    "DEEPSEEK_API_KEY": "DeepSeek API 密钥",
    "DEEPSEEK_BASE_URL": "DeepSeek API 地址",
    "DEEPSEEK_MODEL": "DeepSeek 模型名称",
}


class DeepSeekConfigurationError(ValueError):
    """本地 DeepSeek 配置缺失或无效。"""


@dataclass(frozen=True)
class DeepSeekSettings:
    api_key: str = field(repr=False)
    base_url: str
    model: str


@dataclass(frozen=True)
class ConnectionTestResult:
    connected: bool
    message: str


def load_settings() -> DeepSeekSettings:
    """读取环境变量、项目根目录 .env 或 Streamlit Secrets，不输出密钥。"""
    load_dotenv(dotenv_path=ENV_FILE)

    values = {
        name: (
            os.getenv(name, "").strip()
            or _read_streamlit_secret(name).strip()
        )
        for name in REQUIRED_SETTINGS
    }
    missing = [
        description
        for name, description in REQUIRED_SETTINGS.items()
        if not values[name]
    ]
    if missing:
        missing_variables = ", ".join(
            name for name in REQUIRED_SETTINGS if not values[name]
        )
        descriptions = "、".join(missing)
        raise DeepSeekConfigurationError(
            f"DeepSeek 配置不完整：{descriptions}未设置。"
            f"请在项目根目录的 .env 中配置：{missing_variables}。"
            "部署到 Streamlit Community Cloud 时，请在应用的 Secrets 中配置同名字段。"
        )

    return DeepSeekSettings(
        api_key=values["DEEPSEEK_API_KEY"],
        base_url=values["DEEPSEEK_BASE_URL"],
        model=values["DEEPSEEK_MODEL"],
    )


def _read_streamlit_secret(name: str) -> str:
    try:
        value = st.secrets.get(name, "")
    except StreamlitSecretNotFoundError:
        return ""
    return value if isinstance(value, str) else str(value)


def create_client(
    settings: DeepSeekSettings | None = None,
    *,
    timeout: float = 90.0,
    max_retries: int = 0,
) -> OpenAI:
    """创建 OpenAI 兼容客户端；此操作本身不会发起网络请求。"""
    if settings is None:
        settings = load_settings()
    return OpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url,
        timeout=timeout,
        max_retries=max_retries,
    )


def test_connection() -> ConnectionTestResult:
    """发送一次最小聊天请求以验证 API；调用本函数会产生真实 API 请求。"""
    try:
        settings = load_settings()
    except DeepSeekConfigurationError as exc:
        return ConnectionTestResult(connected=False, message=str(exc))

    try:
        client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=20.0,
        )
        client.chat.completions.create(
            model=settings.model,
            messages=[{"role": "user", "content": "Reply with OK."}],
            max_tokens=5,
        )
    except APIError:
        return ConnectionTestResult(
            connected=False,
            message="DeepSeek API 请求失败。请检查网络、API Key、Base URL、模型名称及账号权限。",
        )

    return ConnectionTestResult(
        connected=True,
        message="DeepSeek API 请求成功，连接测试通过。",
    )
