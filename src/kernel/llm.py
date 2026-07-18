"""LLM 工厂——统一构造聊天模型实例。

所有 Agent 节点通过 ``get_chat_model`` 获取实例。FastAPI 游客请求优先使用请求级
``RuntimeModelConfig``；开发身份和 LangGraph Studio 没有请求上下文，继续读取 `.env`。
"""

from __future__ import annotations

from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from kernel.config import DEEPSEEK_API_KEY, DEEPSEEK_API_URL, DEFAULT_MODEL
from kernel.runtime_model import get_runtime_model


def get_chat_model(
    model: str | None = None, tools: list[Any] | None = None
) -> BaseChatModel:
    """获取聊天模型实例。

    所有场景统一走 OpenAI 兼容端点（``ChatOpenAI``）：模型名含 ``deepseek``
    时用 ``DEEPSEEK_API_KEY`` / ``DEEPSEEK_API_URL``；否则用默认 OpenAI env
    （``OPENAI_API_KEY`` / ``OPENAI_BASE_URL``）。

    Args:
        model: 模型名，``None`` 时用 ``config.DEFAULT_MODEL``。调用方应传场景
            常量（``CHAT_MODEL`` / ``EXTRACTION_MODEL`` / ``RESUME_MODEL`` /
            ``RESEARCH_MODEL``）以支持分场景切换。
        tools: 可选工具列表，提供则 ``bind_tools``。

    Returns:
        ``BaseChatModel`` 实例（已按需 bind_tools）。
    """
    runtime = get_runtime_model()
    if runtime is not None:
        if runtime.api_format != "openai-chat-completions":
            raise ValueError(f"unsupported API format: {runtime.api_format}")
        selected_model = runtime.model
        llm: BaseChatModel = ChatOpenAI(
            model=selected_model,
            api_key=SecretStr(runtime.api_key),
            base_url=runtime.base_url,
        )
    elif model is None:
        selected_model = DEFAULT_MODEL
        llm = _environment_model(selected_model)
    else:
        selected_model = model
        llm = _environment_model(selected_model)

    if tools:
        llm = cast(BaseChatModel, llm.bind_tools(tools))
    return llm


def _environment_model(model: str) -> BaseChatModel:
    """为 Studio 或受控开发身份创建使用服务端 `.env` 的模型。"""
    if "deepseek" in model.lower():
        llm: BaseChatModel = ChatOpenAI(
            model=model,
            api_key=SecretStr(DEEPSEEK_API_KEY) if DEEPSEEK_API_KEY else None,
            base_url=DEEPSEEK_API_URL,
        )
    else:
        llm = ChatOpenAI(model=model)
    return llm
