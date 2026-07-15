"""阶段 3 请求级模型配置和游客/开发凭据来源测试。"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from kernel.llm import get_chat_model
from kernel.runtime_model import (
    RuntimeModelConfig,
    get_runtime_model,
    use_runtime_model,
)
from server.app import _runtime_model_from_request
from server.identity import Principal


def _request(
    *,
    host: str = "localhost",
    scheme: str = "http",
    headers: dict[str, str] | None = None,
) -> Request:
    raw_headers = [
        (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
    ]
    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "scheme": scheme,
        "path": "/v1/chat",
        "raw_path": b"/v1/chat",
        "query_string": b"",
        "headers": [(b"host", host.encode()), *raw_headers],
        "server": (host, 443 if scheme == "https" else 80),
        "client": ("127.0.0.1", 12345),
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


def test_guest_model_headers_are_required_and_http_is_local_only() -> None:
    guest = Principal(id="guest", kind="guest")
    with pytest.raises(HTTPException) as missing:
        _runtime_model_from_request(_request(), guest)
    assert missing.value.detail["code"] == "MODEL_CONFIG_REQUIRED"

    headers = {
        "X-IA-API-Key": "guest-secret",
        "X-IA-Base-URL": "https://models.example/v1/",
        "X-IA-Model": "example-chat",
    }
    runtime = _runtime_model_from_request(_request(headers=headers), guest)
    assert runtime == RuntimeModelConfig(
        api_key="guest-secret",
        base_url="https://models.example/v1",
        model="example-chat",
    )

    with pytest.raises(HTTPException) as insecure:
        _runtime_model_from_request(
            _request(host="public.example", scheme="http", headers=headers), guest
        )
    assert insecure.value.detail["code"] == "HTTPS_REQUIRED"


def test_developer_ignores_browser_model_headers() -> None:
    developer = Principal(id="developer-local", kind="developer")
    assert _runtime_model_from_request(_request(), developer) is None


def test_chat_model_uses_temporary_runtime_and_clears_it() -> None:
    runtime = RuntimeModelConfig(
        api_key="isolated-secret",
        base_url="https://models.example/v1",
        model="runtime-model",
    )
    fake_model = MagicMock()
    with patch("kernel.llm.ChatOpenAI", return_value=fake_model) as constructor:
        with use_runtime_model(runtime):
            assert get_runtime_model() == runtime
            assert get_chat_model("ignored-environment-model") is fake_model
        assert get_runtime_model() is None

    kwargs = constructor.call_args.kwargs
    assert kwargs["model"] == "runtime-model"
    assert kwargs["base_url"] == "https://models.example/v1"
    assert kwargs["api_key"].get_secret_value() == "isolated-secret"
