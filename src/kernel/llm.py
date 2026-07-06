"""LLM 工厂——统一构造聊天模型实例。

所有 agent 节点通过 ``from kernel.llm import get_chat_model`` 引用。模型名与 provider
配置从 ``kernel.config`` 读取（R2 收口，消除散落的硬编码模型名）。

.. note::
   选项 A（重启切换）：改 ``kernel/config.py`` 或对应环境变量后重启即切模型，
   不做 per-request / per-user 注入式多模型（Non-Goal）。
"""

from __future__ import annotations

from typing import Any, cast

from langchain.chat_models import BaseChatModel, init_chat_model

from kernel.config import DEEPSEEK_API_KEY, DEEPSEEK_API_URL, DEFAULT_MODEL


def get_chat_model(
    model: str | None = None, tools: list[Any] | None = None
) -> BaseChatModel:
    """获取聊天模型实例。

    当 ``model`` 含 ``deepseek`` 时，走 OpenAI 兼容端点
    （``DEEPSEEK_API_KEY`` / ``DEEPSEEK_API_URL`` 走 config）；否则交由
    ``init_chat_model`` 默认分派。

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
        llm = init_chat_model(
            model,
            model_provider="openai",
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_API_URL,
        )
    else:
        llm = init_chat_model(model)

    if tools:
        llm = cast(BaseChatModel, llm.bind_tools(tools))
    return llm
