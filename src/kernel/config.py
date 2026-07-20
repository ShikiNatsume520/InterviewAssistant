"""模型与 provider 配置（唯一真相源）。

R2 重构：把散落在 5+ 处的硬编码模型名 ``"deepseek-v4-flash"`` 收口于此。
各 agent 通过 ``from kernel.config import CHAT_MODEL`` 等引用，``kernel.llm`` 也从此
读默认模型与 provider 配置。

实现：``os.getenv`` 读 ``.env``（无新依赖）。未来需 env 校验可换 ``pydantic-settings``。
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

# --------------------------------------------------------------------------- #
# 模型名（各场景可独立配置，默认同 DEFAULT_MODEL）
# --------------------------------------------------------------------------- #

DEFAULT_MODEL: str = os.getenv("IA_DEFAULT_MODEL", "deepseek-v4-flash")
"""默认模型名（所有场景的兜底值，``get_chat_model(model=None)`` 时使用）。"""

CHAT_MODEL: str = os.getenv("IA_CHAT_MODEL", DEFAULT_MODEL)
"""主图 ``chat_node``（``bind_tools``）使用的模型。"""

EXTRACTION_MODEL: str = os.getenv("IA_EXTRACTION_MODEL", DEFAULT_MODEL)
"""长期记忆提取（``save_memory_node``）使用的模型。"""

RESUME_MODEL: str = os.getenv("IA_RESUME_MODEL", DEFAULT_MODEL)
"""简历优化子图使用的模型。"""

RESEARCH_MODEL: str = os.getenv("IA_RESEARCH_MODEL", DEFAULT_MODEL)
"""自主深研子图（outline / distill / finalize）使用的模型。"""

# --------------------------------------------------------------------------- #
# Provider 配置（OpenAI 兼容端点）
# --------------------------------------------------------------------------- #

DEEPSEEK_API_KEY: str | None = os.getenv("DEEPSEEK_API_KEY")
"""DeepSeek（OpenAI 兼容）API 密钥。"""

DEEPSEEK_API_URL: str | None = os.getenv("DEEPSEEK_API_URL")
"""DeepSeek API 基地址。"""

# --------------------------------------------------------------------------- #
# Web 身份与开发人员模式
# --------------------------------------------------------------------------- #

DEV_MODE_ENABLED: bool = os.getenv("IA_DEV_MODE", "false").lower() in {
    "1",
    "true",
    "yes",
}
"""是否启用开发人员登录端点；生产环境默认关闭。"""

DEV_ACCESS_TOKEN: str | None = os.getenv("IA_DEV_ACCESS_TOKEN")
"""开发人员登录凭证；只在登录请求中比较，不写入应用数据库。"""

COOKIE_SECURE: bool = os.getenv("IA_COOKIE_SECURE", "false").lower() in {
    "1",
    "true",
    "yes",
}
"""身份 Cookie 是否仅通过 HTTPS 发送；生产部署必须设为 true。"""
