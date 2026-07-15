"""请求级模型配置上下文；敏感值不得进入 Agent State 或持久化层。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class RuntimeModelConfig:
    """一次图执行使用的 OpenAI-compatible 模型配置。"""

    api_key: str
    base_url: str
    model: str


_runtime_model_config: ContextVar[RuntimeModelConfig | None] = ContextVar(
    "runtime_model_config", default=None
)


@contextmanager
def use_runtime_model(config: RuntimeModelConfig | None) -> Iterator[None]:
    """在当前异步调用上下文临时设置模型配置，并保证结束后清除。"""
    token = _runtime_model_config.set(config)
    try:
        yield
    finally:
        _runtime_model_config.reset(token)


def get_runtime_model() -> RuntimeModelConfig | None:
    """返回当前请求配置；None 表示 Studio/开发身份使用服务端环境。"""
    return _runtime_model_config.get()
