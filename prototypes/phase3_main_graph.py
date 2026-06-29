"""Phase 3 主图总线原型 (v2 — as_tool 路由版)。

核心设计
--------
不再为子图预留 I/O 槽，不再用 ``AgentAction`` 结构化决策。
改用 LLM 原生的 ``bind_tools``：将子图用 ``as_tool()`` 包装为 Tool，
与普通工具一起绑定给 LLM。路由函数负责拦截子图工具调用并分发到对应
wrapper 节点。

Flow 示意
---------
::

  chat_node (bind_tools)
      │  conditional_edges (route_after_chat)
      │
      ├── 无 tool_call ────────────────→ END
      ├── 普通工具 (search/calc 等)  ──→ ToolNode ──→ chat_node
      └── 子智能体 (rag_agent 等)     ──→ wrapper_node ──→ chat_node
                                           │
                                           ├── 从 tool_call.args 映射子图输入
                                           ├── subgraph.invoke()
                                           └── ToolMessage + 额外字段（citations）

状态简化
--------
``MainState`` 只保留：
  - ``messages`` (add_messages)
  - ``citations`` (方便后续直接调用的引用列表)

不再需要 ``rag_subgraph_input`` / ``rag_subgraph_output`` 等每子图 I/O 槽。

依赖
----
- Phase 1/2 的 ``rag_agent`` 子图（只读不写）。
- LLM 走 ``DEEPSEEK_API_URL`` / ``DEEPSEEK_API_KEY`` / ``DEEPSEEK_MODEL`` 环境变量。

运行（项目根）：
    uv run python -m prototypes.phase3_main_graph
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Any, Callable, Literal

from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.tools import Tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

# --------------------------------------------------------------------------- #
# 本原型依赖的正式模块（Phase 1/2 成果，只读不写）
# --------------------------------------------------------------------------- #
from rag_agent.graph import graph as rag_subgraph
from rag_agent.state import Citation

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ============================ 1. 状态 Schema ============================= #

class MainState(TypedDict, total=False):
    """主图状态（简化版，不留子图 I/O 槽）。

    Attributes:
        messages: 对话消息历史（``add_messages`` 累积）。
        citations: 最后检索的结构化引用列表，方便后续节点直接读取。
    """
    messages: Annotated[list[BaseMessage], add_messages]
    citations: list[Citation]


# ======================= 2. SubAgentMetaData =========================== #

class SubAgentMetaData:
    """子智能体注册元数据。

    Attributes:
        name: 注册名（同时也是工具名和 LangGraph 节点名）。
        description: 工具描述，LLM 通过它理解何时调用。
        subgraph: 已编译的 LangGraph 子图实例。
        wrapper_node_fn: 包装节点函数——接收 MainState → 提取 tool_call →
                        invoke 子图 → ToolMessage + 额外字段。
        tool: 用于 LLM ``bind_tools`` 的 Tool 对象（通过 ``as_tool()`` 生成）。
    """

    def __init__(
        self,
        name: str,
        description: str,
        subgraph: Any,
        wrapper_node_fn: Callable[[MainState], dict],
    ) -> None:
        self.name = name
        self.description = description
        self.subgraph = subgraph
        self.wrapper_node_fn = wrapper_node_fn
        self._tool: Tool | None = None

    @property
    def tool(self) -> Tool:
        """用 ``as_tool()`` 将子图包装为 LLM 可调用的 Tool。"""
        if self._tool is None:
            self._tool = self.subgraph.as_tool(
                name=self.name,
                description=self.description,
            )
        return self._tool


# ======================= 3. AgentRegistry ============================== #

class AgentRegistry:
    """子智能体注册中心（简化版）。

    - ``register()``：注册子图元数据。
    - ``get_all_tools()``：返回所有工具的平铺列表（普通工具 + 子图 Tool），
      用于 LLM ``bind_tools``。
    - ``is_sub_agent(name)``：判断工具名是否为子智能体。
    """

    def __init__(self, basic_tools: list[Tool] | None = None) -> None:
        self._sub_agents: dict[str, SubAgentMetaData] = {}
        self.basic_tools: list[Tool] = basic_tools or []

    @property
    def registered(self) -> dict[str, SubAgentMetaData]:
        return dict(self._sub_agents)

    def register(self, meta: SubAgentMetaData) -> None:
        self._sub_agents[meta.name] = meta

    def get_all_tools(self) -> list[Tool]:
        """返回普通工具 + 所有子图 Tool 的平铺列表。"""
        tools = list(self.basic_tools)
        for meta in self._sub_agents.values():
            tools.append(meta.tool)
        return tools

    def is_sub_agent(self, tool_name: str) -> bool:
        return tool_name in self._sub_agents

    def get_sub_agent_names(self) -> set[str]:
        return set(self._sub_agents.keys())


# ======================= 4. 子图包装节点（rag_agent）==================== #

def rag_agent_node(state: MainState) -> dict:
    """rag_agent 包装节点：从 ``tool_call.args`` → invoke 子图 → ToolMessage。"""
    messages = state.get("messages", [])
    if not messages:
        return {"citations": []}

    last_msg = messages[-1]
    tool_calls = getattr(last_msg, "tool_calls", [])
    if not tool_calls:
        return {"citations": []}

    tool_call = tool_calls[0]
    args = tool_call.get("args", {})

    result = rag_subgraph.invoke({
        "search_query": args.get("query", args.get("search_query", "")),
        "search_type": args.get("search_type", "semantic"),
    })

    citations: list[Citation] = result.get("citations_output", [])
    gap_topic: str | None = result.get("gap_topic")

    if gap_topic:
        content = f"未在知识库中找到「{gap_topic}」的相关内容。"
    elif citations:
        parts: list[str] = []
        for i, c in enumerate(citations, 1):
            parts.append(
                f"[{i}] {c['file_path']}(L{c['start_line']}~{c['end_line']})\n"
                f"    {c['content']}"
            )
        content = "\n\n".join(parts)
    else:
        content = "未检索到相关内容。"

    return {
        "messages": [ToolMessage(
            content=content,
            tool_call_id=tool_call["id"],
        )],
        "citations": citations,
    }


# ======================= 5. LLM 工具函数 ============================== #

_llm_instance: ChatOpenAI | None = None


def _get_llm() -> ChatOpenAI:
    global _llm_instance
    if _llm_instance is None:
        base_url = os.environ.get("DEEPSEEK_API_URL", "")
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        _llm_instance = ChatOpenAI(
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=0.0,
            max_completion_tokens=1500,
        )
    return _llm_instance


# ======================= 6. 节点函数 ================================== #

def chat_node(state: MainState, registry: AgentRegistry) -> dict:
    """LLM 决策节点：``bind_tools`` 让 LLM 自主选择工具或直接回答。"""
    all_tools = registry.get_all_tools()
    model = _get_llm().bind_tools(all_tools)

    messages = state.get("messages", [])
    response = model.invoke(messages)

    return {"messages": [response]}


def route_after_chat(state: MainState, registry: AgentRegistry) -> str:
    """``chat_node`` 的条件路由。

    - 无 tool_call → ``END``
    - 工具名属于子智能体 → 路由到对应 wrapper 节点
    - 否则 → ``"tools_node"``（走 ``ToolNode``）
    """
    messages = state.get("messages", [])
    if not messages:
        return END

    last_msg = messages[-1]
    # HumanMessage 没有 tool_calls；AIMessage 也可能无 tool_call
    tool_calls = getattr(last_msg, "tool_calls", [])
    if not tool_calls:
        return END

    tool_name = tool_calls[0]["name"]

    if registry.is_sub_agent(tool_name):
        return tool_name  # 节点名 = 工具名 = 子智能体注册名

    return "tools_node"


# ======================= 7. 主图构建 ================================== #

def _default_registry() -> AgentRegistry:
    """使用默认注册表（仅 rag_agent）构建主图。"""
    registry = AgentRegistry()
    registry.register(SubAgentMetaData(
        name="rag_agent",
        description=(
            "在本地知识库中检索与 Agent 开发、LangGraph 框架、"
            "面试问题相关的技术内容。参数 query 为检索查询词，"
            "search_type 可选 semantic（语义检索）或 keyword（关键词精确匹配）。"
        ),
        subgraph=rag_subgraph,
        wrapper_node_fn=rag_agent_node,
    ))
    return registry


def build_main_graph(registry: AgentRegistry | None = None) -> StateGraph:
    """构建并编译主图。

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
        workflow.add_edge(name, "chat_node")  # 子图执行完毕回 chat_node

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

    workflow.add_edge("tools_node", "chat_node")  # 普通工具执行后回 chat_node

    return workflow.compile(name="MainAgent")


graph = build_main_graph()
"""供 ``langgraph.json`` 发现的可执行图实例（原型阶段）。"""


# ======================= 8. 快速验证入口 ============================== #

if __name__ == "__main__":
    print("=" * 56)
    print("  Phase 3 — 主图总线原型 (v2)")
    print("  核心机制: as_tool + route_after_chat")
    print("=" * 56)

    g = graph
    print(f"\n图名: {g.name}")
    print(f"节点 ({len(g.nodes)}):")
    for n in g.nodes:
        print(f"  └─ {n}")

    gx = g.get_graph()
    print(f"\n连接关系 ({len(gx.edges)}):")
    for e in gx.edges:
        print(f"  {e.source} ──→ {e.target}")

    print("\n[原型就绪] 通过 langgraph dev 注册后可用 Studio / SDK 调试。")
    print("运行: uv run python prototypes/phase3_sdk_test.py")
