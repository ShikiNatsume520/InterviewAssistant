"""LLM 工厂——统一构造聊天模型实例。

迁自 ``src/client.py``（R0 上提到 kernel）。所有 agent 节点通过
``from kernel.llm import get_chat_model`` 引用，消除 ``from src.client`` 的
``src`` 前缀 import（打包不可移植）。

.. note::
   R0 阶段暂保留硬编码 ``"deepseek"`` 子串分派逻辑（与原 ``client.py`` 一致）；
   R2 将把模型名与 provider 配置收进 ``kernel/config.py``。
"""

from __future__ import annotations

import os
from typing import Any, cast

from dotenv import load_dotenv
from langchain.chat_models import BaseChatModel, init_chat_model

load_dotenv()  # 加载 .env 环境变量


def get_chat_model(model_name: str, tools: list[Any] | None = None) -> BaseChatModel:
    """获取聊天模型实例。

    当 ``model_name`` 含 ``deepseek`` 时，走 OpenAI 兼容端点
    （``DEEPSEEK_API_KEY`` / ``DEEPSEEK_API_URL`` 走 env 配置）；
    否则交由 ``init_chat_model`` 默认分派。

    Args:
        model_name: 模型名（R2 后将由 config 提供）。
        tools: 可选工具列表，提供则 ``bind_tools``。

    Returns:
        ``BaseChatModel`` 实例（已按需 bind_tools）。
    """
    if "deepseek" in model_name.lower():
        api_key = os.getenv("DEEPSEEK_API_KEY")
        base_url = os.getenv("DEEPSEEK_API_URL")
        llm = init_chat_model(
            model_name,
            model_provider="openai",
            api_key=api_key,
            base_url=base_url,
        )
    else:
        llm = init_chat_model(model_name)

    if tools:
        llm = cast(BaseChatModel, llm.bind_tools(tools))
    return llm
