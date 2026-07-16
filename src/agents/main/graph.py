"""主图：动态主图总线 + Checkpointer + 长期记忆（Phase 4）。

核心机制
--------
1. ``chat_node``: ``bind_tools(ALL_TOOLS).invoke(messages)`` — LLM 自主决策；
   每次调用时从 Store 读取用户长期记忆，拼接到 system prompt 尾部。
2. ``route_after_chat``: 拦截 ``tool_call`` 分发
   - 无 ``tool_call`` → ``"save_memory"``（记忆提取节点）
   - 子智能体工具 → 对应 ``wrapper`` 节点
   - 其他 → ``"tools_node"``
3. 子智能体节点直接模块级导入编译后的子图，Studio 可静态追踪。
4. 主图 + 子图共用持久化层，实现崩溃恢复与状态回放。

子智能体由 ``agents.main.registry`` 的显式清单 ``REGISTRY`` 布线（R1 重构）。新增子智能体只需：
  a. 在 ``agents/main/tools/`` 写 ``@tool`` + 静态 wrapper 节点（模块顶层 import 已编译子图）
  b. 在 ``agents/main/registry.py`` 的 ``REGISTRY`` 追加一行
  详见 ``docs/agent-registration-guide.md``。
"""

from __future__ import annotations

from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.store.base import BaseStore

from agents.main.nodes.memory import load_memory_context, save_memory_node, set_store
from agents.main.prompts import SYSTEM_PROMPT
from agents.main.registry import REGISTRY
from agents.main.routing import route_after_chat
from agents.main.state import MainState
from agents.main.tools.resume_resources import RESUME_RESOURCE_TOOLS
from kernel.config import CHAT_MODEL
from kernel.llm import get_chat_model
from kernel.logging import dlog, summarize_messages
from kernel.persistence import get_store

# --------------------------------------------------------------------------- #
# 工具列表（后续新增子图时扩展）
# --------------------------------------------------------------------------- #

BASIC_TOOLS: list[Any] = [*RESUME_RESOURCE_TOOLS]
"""普通工具列表（直接由 ToolNode 执行，无需包装节点拦截）。"""

ALL_TOOLS: list[Any] = BASIC_TOOLS + [m["tool"] for m in REGISTRY]
"""LLM bind_tools 的完整工具列表（含子智能体工具，由 ``REGISTRY`` 派生）。"""


# --------------------------------------------------------------------------- #
# 短期记忆：system prompt
# --------------------------------------------------------------------------- #


def _build_system_prompt() -> str:
    """构建 system prompt（仅角色与行为规范，工具定义由 ``bind_tools`` 提供）。"""
    return SYSTEM_PROMPT


# --------------------------------------------------------------------------- #
# 核心节点
# --------------------------------------------------------------------------- #


def _init_chat(store: BaseStore) -> None:
    """初始化 ``chat_node`` 使用的共享 Store。"""
    set_store(store)


def chat_node(state: MainState) -> dict[str, Any]:
    """LLM 决策节点 + 记忆注入。

    每次调用时从 ``agent.memory`` 读取当前用户记忆，拼接到 system prompt 尾部。
    """
    user_id = state.get("user_id", "default")
    dlog(
        "main",
        "chat_node",
        "进入节点",
        user_id=user_id,
        msgs_n=len(state.get("messages", [])),
        msgs=summarize_messages(state.get("messages", [])),
    )
    mc = load_memory_context(user_id)
    system_prompt = _build_system_prompt()
    if mc:
        system_prompt += f"\n\n## Memory\n{mc}"
        dlog("main", "chat_node", "已注入长期记忆", memory_len=len(mc))

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("placeholder", "{messages}"),
        ]
    )
    chat_model = get_chat_model(CHAT_MODEL, tools=ALL_TOOLS)
    chain = prompt | chat_model
    response = chain.invoke({"messages": state.get("messages", [])})
    tcs = getattr(response, "tool_calls", []) or []
    if tcs:
        dlog(
            "main", "chat_node", "LLM 决策调用工具", tools=[t.get("name") for t in tcs]
        )
    else:
        c = (
            response.content
            if isinstance(response.content, str)
            else str(response.content)
        )
        dlog("main", "chat_node", "LLM 直接回复", reply_len=len(c))
    return {"messages": [response]}


# --------------------------------------------------------------------------- #
# 主图构建
# --------------------------------------------------------------------------- #


def build_main_graph(
    checkpointer: Any = None,
    store: BaseStore | None = None,
) -> Any:
    """构建并编译主图。

    Args:
        checkpointer: Checkpointer 实例（``SqliteSaver``），``False`` 表示使用平台默认。
        store: Store 实例（``SqliteStore``），为 ``None`` 时创建默认。

    Returns:
        编译后的 ``CompiledStateGraph``。
    """
    if store is None:
        store = get_store()

    # 初始化全局状态（chat_model + store）
    _init_chat(store)
    dlog(
        "main",
        "build_main_graph",
        "编译主图",
        checkpointer=type(checkpointer).__name__ if checkpointer else "None",
        tools=[getattr(t, "name", str(t)) for t in ALL_TOOLS],
    )

    workflow = StateGraph(MainState)

    # ── 节点：chat + 普通工具 + 记忆 + 子智能体（遍历 REGISTRY） ──
    workflow.add_node("chat_node", chat_node)
    workflow.add_node("tools_node", ToolNode(BASIC_TOOLS))
    workflow.add_node("save_memory", save_memory_node)
    for meta in REGISTRY:
        workflow.add_node(meta["name"], meta["node"])

    # ── 连线 ──
    workflow.set_entry_point("chat_node")

    # path_map 声明 conditional edge 的所有可达节点——route_after_chat 运行时
    # 返回 list[Send] 直达节点（不经 path_map 映射），但 Studio 静态分析靠
    # path_map 画 chat_node → 各节点的入边，故必须列出全部可达节点。
    path_map: dict[Any, str] = {
        "save_memory": "save_memory",
        "tools_node": "tools_node",
    }
    path_map.update({m["route_key"]: m["name"] for m in REGISTRY})

    workflow.add_conditional_edges("chat_node", route_after_chat, path_map)

    workflow.add_edge("tools_node", "chat_node")
    workflow.add_edge("save_memory", END)
    for meta in REGISTRY:
        workflow.add_edge(meta["name"], "chat_node")

    return workflow.compile(name="MainAgent", checkpointer=checkpointer)


# =========================================================================== #
# 模块导出 — 供 ``langgraph.json`` 发现
# =========================================================================== #

graph = build_main_graph(checkpointer=False)
"""供 ``langgraph.json`` 及 ``__init__.py`` 导出的可执行图实例（Studio 调试用，无自定义 checkpointer）。"""
