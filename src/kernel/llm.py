"""LLM 工厂——统一构造聊天模型实例。

所有 agent 节点通过 ``from kernel.llm import get_chat_model`` 引用。模型名与 provider
配置从 ``kernel.config`` 读取（R2 收口，消除散落的硬编码模型名）。

.. note::
   选项 A（重启切换）：改 ``kernel/config.py`` 或对应环境变量后重启即切模型，
   不做 per-request / per-user 注入式多模型（Non-Goal）。
"""

from __future__ import annotations

from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from kernel.config import DEEPSEEK_API_KEY, DEEPSEEK_API_URL, DEFAULT_MODEL


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
    if model is None:
        model = DEFAULT_MODEL
    if "deepseek" in model.lower():
        llm: BaseChatModel = ChatOpenAI(
            model=model,
            api_key=SecretStr(DEEPSEEK_API_KEY) if DEEPSEEK_API_KEY else None,
            base_url=DEEPSEEK_API_URL,
        )
    else:
        llm = ChatOpenAI(model=model)

    if tools:
        llm = cast(BaseChatModel, llm.bind_tools(tools))
    return llm
