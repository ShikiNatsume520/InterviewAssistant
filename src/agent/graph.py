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

无动态注册表。新增子智能体时只需：
  a. 定义 @tool + 包装节点函数
  b. 将 tool 加入 ``ALL_TOOLS``
  c. 在 ``route_after_chat`` 和 ``path_map`` 中添加路由
  d. 在 ``build_main_graph`` 中 ``add_node`` + ``add_edge``
"""

from __future__ import annotations

from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.store.base import BaseStore

from agent.debug import dlog, summarize_messages
from agent.memory import load_memory_context, save_memory_node, set_store
from agent.persistence import get_store
from agent.state import MainState
from agent.tools.rag_agent import rag_agent, rag_agent_node
from agent.tools.resume_agent import resume_agent, resume_agent_node
from src.client import get_chat_model

# --------------------------------------------------------------------------- #
# 工具列表（后续新增子图时扩展）
# --------------------------------------------------------------------------- #

BASIC_TOOLS: list[Any] = []
"""普通工具列表（直接由 ToolNode 执行，无需包装节点拦截）。"""

ALL_TOOLS: list[Any] = BASIC_TOOLS + [rag_agent, resume_agent]
"""LLM bind_tools 的完整工具列表（含子智能体工具）。"""


# --------------------------------------------------------------------------- #
# 短期记忆：system prompt
# --------------------------------------------------------------------------- #


def _build_system_prompt() -> str:
    """构建 system prompt（仅角色与行为规范，工具定义由 ``bind_tools`` 提供）。"""
    return (
        "你是一名专业的面试知识助手与简历优化专家，掌握 Agent 开发、LangGraph "
        "框架以及面试准备方面的知识。\n"
        "\n"
        "工作准则：\n"
        "1. 知识问答必须基于 rag_agent 检索结果回答，严禁编造信息。回答时引用来源。\n"
        "2. 当用户明确要求优化/修改简历时，调用 resume_agent 工具进入简历优化子流程，"
        "由子流程完成多轮 CRUD 优化；普通闲聊直接回复即可。\n"
        "3. 工具使用时机和参数由工具自身的描述和 schema 定义，遵循即可。"
    )


# --------------------------------------------------------------------------- #
# 核心节点
# --------------------------------------------------------------------------- #


# chat_node 使用的模型（bind_tools 后），由 _init_chat 初始化
_chat_model: Any = None


def _init_chat(store: BaseStore) -> None:
    """初始化 ``chat_node`` 依赖的全局状态。

    在 ``build_main_graph`` 中调用，Store 实例在此注入。
    """
    global _chat_model
    _chat_model = get_chat_model("deepseek-v4-flash", tools=ALL_TOOLS)
    set_store(store)


def chat_node(state: MainState) -> dict[str, Any]:
    """LLM 决策节点 + 记忆注入。

    每次调用时从 ``agent.memory`` 读取当前用户记忆，拼接到 system prompt 尾部。
    """
    if _chat_model is None:
        raise RuntimeError("chat_node 未初始化——请确保 _init_chat() 已被调用")

    user_id = state.get("user_id", "default")
    dlog("main", "chat_node", "进入节点", user_id=user_id,
         msgs=summarize_messages(state.get("messages", [])))
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
    chain = prompt | _chat_model
    response = chain.invoke({"messages": state.get("messages", [])})
    tcs = getattr(response, "tool_calls", []) or []
    if tcs:
        dlog("main", "chat_node", "LLM 决策调用工具",
             tools=[t.get("name") for t in tcs])
    else:
        c = response.content if isinstance(response.content, str) else str(response.content)
        dlog("main", "chat_node", "LLM 直接回复", reply_len=len(c))
    return {"messages": [response]}


def route_after_chat(state: MainState) -> str:
    """``chat_node`` 的条件路由。

    检查最后一条消息的 ``tool_calls``:

    - 无 ``tool_call`` → ``"save_memory"``
    - 工具名为子智能体 → 对应 ``wrapper`` 节点（后续新增时扩展）
    - 否则 → ``"tools_node"``
    """
    messages = state.get("messages", [])
    if not messages:
        dlog("main", "route_after_chat", "无消息 → save_memory")
        return "save_memory"

    tool_calls = getattr(messages[-1], "tool_calls", [])
    if not tool_calls:
        dlog("main", "route_after_chat", "无 tool_call → save_memory")
        return "save_memory"

    tool_name: str = str(tool_calls[0].get("name", ""))

    # 子智能体路由（后续新增子图时扩展）
    if tool_name == "rag_agent":
        dlog("main", "route_after_chat", f"→ rag_agent (tool={tool_name})")
        return "rag_agent"
    if tool_name == "resume_agent":
        dlog("main", "route_after_chat", f"→ resume_agent (tool={tool_name})")
        return "resume_agent"

    dlog("main", "route_after_chat", f"→ tools_node (tool={tool_name})")
    return "tools_node"


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
    dlog("main", "build_main_graph", "编译主图",
         checkpointer=type(checkpointer).__name__ if checkpointer else "None",
         tools=[getattr(t, "name", str(t)) for t in ALL_TOOLS])

    workflow = StateGraph(MainState)

    # ── 节点：静态注册，全部加载 ──
    workflow.add_node("chat_node", chat_node)
    workflow.add_node("rag_agent", rag_agent_node)
    workflow.add_node("resume_agent", resume_agent_node)
    workflow.add_node("tools_node", ToolNode(BASIC_TOOLS))
    workflow.add_node("save_memory", save_memory_node)

    # ── 连线 ──
    workflow.set_entry_point("chat_node")

    path_map: dict[Any, str] = {
        "save_memory": "save_memory",
        "tools_node": "tools_node",
        "rag_agent": "rag_agent",
        "resume_agent": "resume_agent",
    }

    workflow.add_conditional_edges(
        "chat_node",
        route_after_chat,
        path_map,
    )

    workflow.add_edge("rag_agent", "chat_node")
    workflow.add_edge("resume_agent", "chat_node")
    workflow.add_edge("tools_node", "chat_node")
    workflow.add_edge("save_memory", END)

    return workflow.compile(
        name="MainAgent",
        checkpointer=checkpointer,
    )


# =========================================================================== #
# 模块导出 — 供 ``langgraph.json`` 发现
# =========================================================================== #

graph = build_main_graph(checkpointer=False)
"""供 ``langgraph.json`` 及 ``__init__.py`` 导出的可执行图实例（Studio 调试用，无自定义 checkpointer）。"""
