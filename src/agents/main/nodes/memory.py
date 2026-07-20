"""长期记忆模块 — Store I/O 与 LLM 提取。

职责
----
- 管理 ``_store`` 全局实例（由 ``set_store()`` 初始化）。
- ``load_memory_context(user_id)``：读记忆（带进程内缓存）。
- ``save_memory_node(state)``：LangGraph 节点，提取事实写入 Store。

所有全局变量集中在模块内，不污染 ``graph.py``。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.store.base import BaseStore

from agents.main.prompts import build_extraction_prompt
from agents.main.state import MainState
from kernel.config import EXTRACTION_MODEL
from kernel.llm import get_chat_model
from kernel.logging import dlog

# ═══════════════════════════════════════════════════════════════════════ #
# 全局变量（由 build_main_graph → _init_chat 初始化）
# ═══════════════════════════════════════════════════════════════════════ #

_memory_cache: dict[str, str] = {}
"""记忆文本缓存（key: user_id），避免同一轮对话多次查 Store。"""

_store: BaseStore | None = None
"""全局 Store 实例（持久化 ``SqliteStore``），由 ``set_store()`` 设置。"""

"""记忆提取 LLM（惰性初始化）。"""


# ═══════════════════════════════════════════════════════════════════════ #
# 公开接口
# ═══════════════════════════════════════════════════════════════════════ #


def set_store(store: BaseStore) -> None:
    """设置 Store 实例。

    在 ``build_main_graph → _init_chat`` 中调用，将主图创建的 Store 注入本模块。
    """
    global _store
    _store = store


def load_memory_context(user_id: str) -> str:
    """从 Store 读取用户长期记忆（带进程内缓存）。

    记忆仅在最开始执行主图时加载一次，中间 ``chat_node`` 循环命中缓存；
    ``save_memory_node`` 写入新事实后清除缓存，下次自动重新加载。

    Args:
        user_id: 用户标识。

    Returns:
        格式化的 ``## Memory`` 板块文本，空字符串表示无记忆。
    """
    if user_id in _memory_cache:
        return _memory_cache[user_id]

    items = _store.search(("memory", user_id, "facts"))  # type: ignore[union-attr]
    if not items:
        _memory_cache[user_id] = ""
        return ""

    parts: list[str] = []
    for item in items:
        val = item.value if hasattr(item, "value") else item
        if isinstance(val, dict) and "fact" in val:
            cat = val.get("category", "general")
            parts.append(f"- [{cat}] {val['fact']}")

    result = "\n".join(parts) if parts else ""
    _memory_cache[user_id] = result
    return result


def save_memory_node(state: MainState) -> dict[str, Any]:
    """提取本轮对话中的新事实 → 写入 Store。

    只处理 ``HumanMessage`` + ``AIMessage`` 两类消息；
    用 LLM 去重后写入 ``Store.put()``。
    """
    messages = state.get("messages", [])
    user_id = state.get("user_id", "default")
    dlog("main", "save_memory", "进入节点", user_id=user_id, msgs_n=len(messages))

    # 过滤：只保留 User + AI 消息
    relevant = [m for m in messages if isinstance(m, (HumanMessage, AIMessage))]
    if not relevant:
        dlog("main", "save_memory", "无 User/AI 消息，跳过")
        return {}

    # 读取已有事实用于去重
    existing_items = _store.search(("memory", user_id, "facts"))  # type: ignore[union-attr]
    existing_facts: list[str] = []
    for item in existing_items:
        val = item.value if hasattr(item, "value") else item
        if isinstance(val, dict) and "fact" in val:
            existing_facts.append(val["fact"])

    existing_str = (
        "\n".join(f"- {f}" for f in existing_facts)
        if existing_facts
        else "(无已有事实)"
    )
    dlog("main", "save_memory", "已有事实", existing_n=len(existing_facts))

    # 格式化对话
    conv_parts: list[str] = []
    for m in relevant:
        prefix = "用户" if isinstance(m, HumanMessage) else "AI"
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", []):
            tnames = ", ".join(tc.get("name", "") for tc in m.tool_calls)
            content = f"[调用了工具: {tnames}]"
        else:
            content = m.content if isinstance(m.content, str) and m.content else ""
        conv_parts.append(f"[{prefix}]: {content}")
    conv_text = "\n\n".join(conv_parts)

    # LLM 提取
    extraction_llm = get_chat_model(EXTRACTION_MODEL)
    prompt = build_extraction_prompt(
        existing_facts=existing_str,
        conversation=conv_text,
    )
    dlog("main", "save_memory", "调用 LLM 提取事实")
    response = extraction_llm.invoke(prompt)
    raw = (
        response.content if isinstance(response.content, str) else str(response.content)
    )
    dlog("main", "save_memory", "LLM 返回", raw_preview=raw[:200])

    # 清理可能的 markdown 代码块标记
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if "```" in raw:
            raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()

    # 解析 JSON
    try:
        new_facts: list[dict[str, Any]] = json.loads(raw)
        if not isinstance(new_facts, list):
            new_facts = []
    except json.JSONDecodeError:
        dlog("main", "save_memory", "JSON 解析失败，跳过", raw_preview=raw[:200])
        return {}

    # 写入 Store（按已有事实去重）
    known = set(existing_facts)
    written: list[dict[str, Any]] = []
    for f in new_facts:
        if not isinstance(f, dict) or "fact" not in f:
            continue
        text = f["fact"].strip()
        if not text or text in known:
            continue
        entry = {
            "fact": text,
            "category": f.get("category", "general"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _store.put(("memory", user_id, "facts"), f"fact_{uuid4().hex[:8]}", entry)  # type: ignore[union-attr]
        known.add(text)
        written.append(entry)
        dlog("main", "save_memory", "写入 Store", fact=text, category=entry["category"])

    dlog(
        "main",
        "save_memory",
        "完成",
        extracted_n=len(new_facts),
        written_n=len(written),
    )

    # 写入新事实后清除缓存，下一次 chat_node 自动重新加载
    _memory_cache.pop(user_id, None)

    return {}
