from __future__ import annotations

import sys
from pathlib import Path

from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, OpenAI

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.deepseek_client import DeepSeekConfigurationError, load_settings


def _http_error_hint(status_code: int) -> str:
    if status_code in (401, 403):
        return "请检查 API Key 是否正确，以及账号是否有 API 使用权限。"
    if status_code == 404:
        return "请检查 Base URL 和模型名称是否正确。"
    if status_code == 429:
        return "请求频率或额度可能受限，请检查账号状态后再试。"
    return "请检查 DeepSeek 服务状态及本地 API 配置。"


def main() -> int:
    try:
        settings = load_settings()
    except DeepSeekConfigurationError as exc:
        print(f"配置错误：{exc}")
        print("请检查项目根目录的 .env 文件；不要在聊天或终端输出 API Key。")
        return 1

    try:
        client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=20.0,
            max_retries=0,
        )
        response = client.chat.completions.create(
            model=settings.model,
            messages=[
                {"role": "user", "content": "请只回复：连接成功"},
            ],
            max_tokens=20,
        )
    except APIStatusError as exc:
        print(f"真实 API 请求失败：HTTP {exc.status_code}。")
        print(_http_error_hint(exc.status_code))
        return 1
    except APITimeoutError:
        print("真实 API 请求超时（20 秒）。请检查网络后再重试。")
        return 1
    except APIConnectionError:
        print("无法连接 DeepSeek API。请检查网络和 DEEPSEEK_BASE_URL。")
        return 1
    except APIError:
        print("DeepSeek API 请求失败。请检查模型配置及账号状态。")
        return 1
    except ValueError:
        print("DeepSeek 客户端配置无效。请检查 .env 中的 Base URL 和模型名称。")
        return 1

    content = response.choices[0].message.content
    print("真实 API 请求成功：已收到 DeepSeek 模型响应。")
    if content:
        print(f"模型回复：{content.strip()}")
    else:
        print("请求已成功，但响应中没有文本内容。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
