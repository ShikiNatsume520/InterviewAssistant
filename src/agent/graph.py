"""主图：动态主图总线（``as_tool`` 路由版）。

核心机制
--------
1. ``chat_node``: ``llm.bind_tools(all_tools).invoke(messages)`` — LLM 自主决策。
2. ``route_after_chat``: 拦截 ``tool_call`` 分发
   - 无 ``tool_call`` → ``END``
   - 普通工具 → ``ToolNode``
   - 子智能体工具 → 对应 ``wrapper`` 节点
3. ``wrapper`` 节点：解析 ``tool_call.args`` → invoke 子图 →
   ``ToolMessage`` + 额外字段。
"""

from __future__ import annotations

import os
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from agent.registry import AgentRegistry, SubAgentMetaData
from agent.state import MainState
from agent.tools.rag_agent import rag_agent, rag_agent_node
from src.client import get_chat_model


# --------------------------------------------------------------------------- #
# 节点函数
# --------------------------------------------------------------------------- #

chat_model = None


def _build_system_prompt() -> str:
    """构建 system prompt（仅角色与行为规范，工具定义由 ``bind_tools`` 提供）。"""
    return (
        "你是一名专业的面试知识助手，掌握 Agent 开发、LangGraph 框架以及面试准备方面的知识。\n"
        "\n"
        "工作准则：\n"
        "1. 必须基于检索结果回答，严禁编造信息。回答时引用来源。\n"
        "2. 工具使用时机和参数由工具自身的描述和 schema 定义，遵循即可。"
    )


def chat_node(state: MainState, registry: AgentRegistry) -> dict[str, Any]:
    """LLM 决策节点：注入 system prompt + ``bind_tools`` 让 LLM 自主决策。

    Args:
        state: 当前图状态。
        registry: 注册中心（提供 ``get_all_tools``）。

    Returns:
        更新后的消息（AIMessage，可能含 ``tool_calls``）及当前 system_prompt。
    """
    global chat_model
    all_tools = registry.get_all_tools()
    if chat_model is None:
        chat_model = get_chat_model("deepseek-v4-flash", all_tools)

    # 动态组装系统提示词，下面的方式能够在调用api时，自动把system prompt 塞入到messages的开头。这样做的好处是避免每个checkpointer都存储这个固定不变的system prompt
    system_prompt = _build_system_prompt()
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("placeholder", "{messages}"),
    ])
    chain = prompt | chat_model

    response = chain.invoke({"messages": state.get("messages", [])})
    return {"messages": [response]}


def route_after_chat(state: MainState, registry: AgentRegistry) -> str:
    """``chat_node`` 的条件路由。

    检查最后一条消息的 ``tool_calls``:

    - 无 ``tool_call`` → ``END``
    - 工具名在注册表中（子智能体） → 路由到同名 ``wrapper`` 节点
    - 否则（普通工具） → ``"tools_node"``
    """
    messages = state.get("messages", [])
    if not messages:
        return END

    tool_calls = getattr(messages[-1], "tool_calls", [])
    if not tool_calls:
        return END

    tool_name: str = str(tool_calls[0].get("name", ""))
    if registry.is_sub_agent(tool_name):
        return tool_name  # 节点名 = 工具名 = 注册名

    return "tools_node"


# --------------------------------------------------------------------------- #
# 默认注册表
# --------------------------------------------------------------------------- #


def _default_registry() -> AgentRegistry:
    """构建默认注册表（当前仅 rag_agent 一个子智能体）。"""
    registry = AgentRegistry()
    registry.register(
        SubAgentMetaData(
            name="rag_agent",
            tool=rag_agent,
            wrapper_node_fn=rag_agent_node,
        )
    )
    return registry


# --------------------------------------------------------------------------- #
# 主图构建
# --------------------------------------------------------------------------- #


def build_main_graph(
    registry: AgentRegistry | None = None,
) -> Any:
    """构建并编译主图（返回 ``CompiledStateGraph``，但类型桩限制用 ``Any`` 避免误报）。

    Args:
        registry: 预配置的子智能体注册表。为 ``None`` 时使用默认注册。

    Returns:
        编译后的 ``CompiledStateGraph``。
    """
    if registry is None:
        registry = _default_registry()

    workflow = StateGraph(MainState)

    # ── 基础节点 ──────────────────────────────────────────────────────
    workflow.add_node("chat_node", lambda state: chat_node(state, registry))
    workflow.add_node("tools_node", ToolNode(registry.basic_tools))

    # ── 动态注册子图包装节点 ──────────────────────────────────────────
    for name, meta in registry.registered.items():
        workflow.add_node(name, meta.wrapper_node_fn)
        workflow.add_edge(name, "chat_node")

    # ── 连线 ──────────────────────────────────────────────────────────
    workflow.set_entry_point("chat_node")

    # 动态构建 path_map（子图名 / tools_node / END）
    path_map: dict[str, str] = {"tools_node": "tools_node", END: END}
    for name in registry.get_sub_agent_names():
        path_map[name] = name

    workflow.add_conditional_edges(
        "chat_node",
        lambda state: route_after_chat(state, registry),
        path_map,
    )

    workflow.add_edge("tools_node", "chat_node")

    return workflow.compile(name="MainAgent")


# =========================================================================== #
# 模块导出 — 供 ``langgraph.json`` 发现
# =========================================================================== #

graph = build_main_graph()
"""供 ``langgraph.json`` 及 ``__init__.py`` 导出的可执行图实例。"""
