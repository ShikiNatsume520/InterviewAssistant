#!/usr/bin/env python3
"""
Phase 4 原型 — 检查点持久化 + 长期记忆

演示内容
--------
1. SqliteSaver checkpointer 绑定主图 + 子图
2. chat_node 从 Store 读取记忆 → 拼接到 system_prompt 尾部 ``## Memory``
3. save_memory_node 提取 UserMessage + AIMessage → LLM 去重 → Store.put()
4. 崩溃恢复（``get_state_history()`` → 从最后一个 checkpoint 续跑）
5. 跨会话记忆（同一 user_id，不同 thread_id）

用法
----
    python prototypes/phase4_persistence.py

注意
----
- 需要已安装 ``langgraph-checkpoint-sqlite``
- 原型数据库文件生成在 ``prototypes/phase4_checkpoints.db``
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import uuid4

import sqlite3

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.store.memory import InMemoryStore
from typing_extensions import TypedDict

# --------------------------------------------------------------------------- #
# 项目模块 — 通过项目根目录导入
# --------------------------------------------------------------------------- #
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.client import get_chat_model

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
PROTO_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(PROTO_DIR, "phase4_checkpoints.db")

# --------------------------------------------------------------------------- #
# MainState（加上了 user_id）
# --------------------------------------------------------------------------- #


class MainState(TypedDict, total=False):
    """主图状态 — Phase 4 新增 ``user_id``。"""

    messages: Annotated[list[BaseMessage], add_messages]
    citations: list[dict]
    user_id: str


# --------------------------------------------------------------------------- #
# 工具定义 — mock rag_agent
# --------------------------------------------------------------------------- #


@tool
def rag_agent_tool(
    query: str = "",
    search_type: str = "semantic",
) -> str:
    """在本地知识库中检索 Agent / LangGraph / 面试相关内容。

    当用户询问具体技术概念时调用此工具。
    """
    raise RuntimeError("应被 route_after_chat 拦截，不进入 ToolNode")


# --------------------------------------------------------------------------- #
# Mock RAG 子图 — 演示 config 传递 + checkpointer 链
# --------------------------------------------------------------------------- #


class MockRAGState(TypedDict, total=False):
    search_query: str
    search_type: str


def build_mock_rag_graph(checkpointer: SqliteSaver) -> Any:
    """构建模拟 RAG 子图（演示 checkpointer 链继承）。"""
    g = StateGraph(MockRAGState)

    def retrieve_node(state: MockRAGState) -> dict:
        print(f"  [MockRAG-子图] 收到检索: 「{state.get('search_query')}」")
        return {
            "search_query": state.get("search_query"),
            "search_type": state.get("search_type"),
        }

    g.add_node("retrieve", retrieve_node)
    g.set_entry_point("retrieve")
    g.add_edge("retrieve", END)
    return g.compile(name="MockRAGAgent", checkpointer=checkpointer)


def build_rag_agent_wrapper(checkpointer: SqliteSaver) -> Any:
    """构建 rag_agent 包装节点（通过闭包持有 checkpointer 引用）。

    Args:
        checkpointer: SqliteSaver 实例，用于子图编译。

    Returns:
        可调用的包装节点函数。
    """
    # 预编译 mock rag 子图（只一次）
    mock_rag = build_mock_rag_graph(checkpointer)

    def _wrapper(state: MainState, config: RunnableConfig) -> dict:
        """rag_agent 包装节点，处理所有 ``tool_calls``。"""
        messages = state.get("messages", [])
        if not messages:
            return {}

        last_msg = messages[-1]
        tool_calls = getattr(last_msg, "tool_calls", [])
        if not tool_calls:
            return {}

        # 处理所有 tool_calls（应对 LLM 可能并行发起多个调用）
        tool_messages: list[ToolMessage] = []
        all_citations: list[dict] = []

        for tc in tool_calls:
            args = tc.get("args", {})
            query = args.get("query", args.get("search_query", ""))
            search_type = args.get("search_type", "semantic")

            _result = mock_rag.invoke(
                {"search_query": query, "search_type": search_type},
                config,
            )

            fake_citation = {
                "file_path": "prototype.md",
                "start_line": 1,
                "end_line": 3,
                "content": f"关于「{query}」的模拟检索说明。",
            }

            tool_messages.append(
                ToolMessage(
                    content=f"从知识库中找到以下内容：\n\n[{fake_citation['file_path']}]"
                    f"(L{fake_citation['start_line']}~{fake_citation['end_line']})\n"
                    f"    {fake_citation['content']}",
                    tool_call_id=tc["id"],
                )
            )
            all_citations.append(fake_citation)

        return {
            "messages": tool_messages,
            "citations": all_citations,
        }

    return _wrapper


# --------------------------------------------------------------------------- #
# 长期记忆 — Store I/O
# --------------------------------------------------------------------------- #

EXTRACTION_PROMPT = """你是一个用户画像分析师。分析以下对话，提取关于用户的**新**事实性知识。

只提取**明确可推断的**、**跨会话有用的**信息：
- 用户背景（职业、经验、技术栈）
- 正在学习的内容
- 具体兴趣方向
- 目标或需求
- 偏好或习惯

=== 已有事实（请去重，不要重复提取） ===
{existing_facts}

=== 对话（仅用户 + AI 消息） ===
{conversation}

---

返回 **JSON 数组**（不要 markdown 代码块标记，只返回纯 JSON文本）：
每个元素格式：{{"fact": "事实描述", "category": "background|interest|goal|weakness|preference"}}

如果无新事实，返回 []。"""


def load_memory_context(user_id: str, store: InMemoryStore) -> str:
    """从 Store 读取用户画像，返回 ``## Memory`` 板块的文本。

    Args:
        user_id: 用户标识。
        store: Store 实例。

    Returns:
        格式化的记忆文本（空字符串表示无记忆）。
    """
    items = store.search(("memory", user_id, "facts"))
    if not items:
        return ""

    parts: list[str] = []
    for item in items:
        val = item.value if hasattr(item, "value") else item
        if isinstance(val, dict) and "fact" in val:
            cat = val.get("category", "general")
            parts.append(f"- [{cat}] {val['fact']}")

    return "\n".join(parts) if parts else ""


def save_memory_node(state: MainState, *, store: InMemoryStore) -> dict:
    """提取本轮对话中的新事实 → 写入 Store。

    只处理 UserMessage + AIMessage；用 LLM 去重后写入。
    """
    messages = state.get("messages", [])
    user_id = state.get("user_id", "default")

    # 过滤：只保留 User + AI 消息
    relevant = [m for m in messages if isinstance(m, (HumanMessage, AIMessage))]
    if not relevant:
        return {}

    # 读取已有事实用于去重
    existing_items = store.search(("memory", user_id, "facts"))
    existing_facts: list[str] = []
    for item in existing_items:
        val = item.value if hasattr(item, "value") else item
        if isinstance(val, dict) and "fact" in val:
            existing_facts.append(val["fact"])

    existing_str = "\n".join(f"- {f}" for f in existing_facts) if existing_facts else "(无已有事实)"

    # 格式化对话
    conv_lines: list[str] = []
    for m in relevant:
        prefix = "用户" if isinstance(m, HumanMessage) else "AI"
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", []):
            # tool call 消息的 content 通常为空，改用工具名说明
            tnames = ", ".join(tc.get("name", "") for tc in m.tool_calls)
            content = f"[调用了工具: {tnames}]"
        else:
            content = m.content if isinstance(m.content, str) else str(m.content) if m.content else ""
        conv_lines.append(f"[{prefix}]: {content}")
    conv_text = "\n\n".join(conv_lines)

    # LLM 提取
    extraction_llm = get_chat_model("deepseek-v4-flash")
    prompt = EXTRACTION_PROMPT.format(
        existing_facts=existing_str,
        conversation=conv_text,
    )
    response = extraction_llm.invoke(prompt)
    raw = response.content if isinstance(response.content, str) else str(response.content)

    # 清理可能的 markdown 代码块标记
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if "```" in raw:
            raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()

    # 解析 JSON
    try:
        new_facts: list[dict] = json.loads(raw)
        if not isinstance(new_facts, list):
            new_facts = []
    except json.JSONDecodeError:
        print(f"  [Memory] !! LLM 返回非 JSON，跳过: {raw[:120]}...")
        return {}

    # 写入 Store（按已有事实去重）
    known = set(existing_facts)
    written = 0
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
        store.put(("memory", user_id, "facts"), f"fact_{uuid4().hex[:8]}", entry)
        known.add(text)
        written += 1

    if written:
        print(f"  [Memory] ++ 提取并写入 {written} 条新事实")
    else:
        print(f"  [Memory] -- 无新事实")
    return {}


# --------------------------------------------------------------------------- #
# 主图节点
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT_BASE = (
    "你是一名专业的面试知识助手，掌握 Agent 开发、LangGraph 框架以及面试准备方面的知识。\n"
    "\n"
    "工作准则：\n"
    "1. 必须基于检索结果回答，严禁编造信息。回答时引用来源。\n"
    "2. 工具使用时机和参数由工具自身的描述和 schema 定义，遵循即可。\n"
    "3. 当需要检索本地知识库时，使用 rag_agent_tool 工具。"
)


def build_chat_node(
    store: InMemoryStore,
    tools: list,
) -> Any:
    """构建 chat_node（通过闭包持有 store 引用）。

    Args:
        store: Store 实例（用于读取记忆）。
        tools: ``bind_tools`` 的工具列表。

    Returns:
        可调用的节点函数。
    """
    # 提前绑定工具（只初始化一次）
    chat_model = get_chat_model("deepseek-v4-flash")
    chat_model = chat_model.bind_tools(tools)

    def _chat_node(state: MainState) -> dict:
        # 1. 加载记忆 → 拼接到 system prompt 尾部
        user_id = state.get("user_id", "default")
        mc = load_memory_context(user_id, store)
        system_prompt = SYSTEM_PROMPT_BASE
        if mc:
            system_prompt += f"\n\n## Memory\n{mc}"

        # 2. 标准 chat_node 流程
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("placeholder", "{messages}"),
        ])
        chain = prompt | chat_model
        response = chain.invoke({"messages": state.get("messages", [])})
        return {"messages": [response]}

    return _chat_node


def route_after_chat(state: MainState) -> str:
    """条件路由。

    无 ``tool_call`` → ``"save_memory"``（而非直接 END）
    工具名为子智能体 → 对应 wrapper 节点名
    否则 → ``"tools_node"``
    """
    messages = state.get("messages", [])
    if not messages:
        return "save_memory"

    tool_calls = getattr(messages[-1], "tool_calls", [])
    if not tool_calls:
        return "save_memory"

    name: str = str(tool_calls[0].get("name", ""))
    if name == "rag_agent_tool":
        return "rag_agent_wrapper"

    return "tools_node"


# --------------------------------------------------------------------------- #
# 主图构建
# --------------------------------------------------------------------------- #


def build_main_graph(
    checkpointer: SqliteSaver,
    store: InMemoryStore,
) -> Any:
    """构建并编译主图（带 checkpointer + store）。

    Args:
        checkpointer: SqliteSaver 实例。
        store: InMemoryStore 实例。

    Returns:
        编译后的 CompiledStateGraph。
    """
    # 工具列表（当前只有 rag_agent）
    tools = [rag_agent_tool]

    # 构建节点
    chat_node = build_chat_node(store, tools)
    rag_agent_node = build_rag_agent_wrapper(checkpointer)

    # Store 注入：通过 lambda 闭包（不依赖 LangGraph 框架自动注入）
    def _save_memory(state: MainState) -> dict:
        return save_memory_node(state, store=store)

    workflow = StateGraph(MainState)

    # ── 节点 ──
    workflow.add_node("chat_node", chat_node)
    workflow.add_node("rag_agent_wrapper", rag_agent_node)
    workflow.add_node("tools_node", ToolNode(tools))
    workflow.add_node("save_memory", _save_memory)

    # ── 连线 ──
    workflow.set_entry_point("chat_node")

    workflow.add_conditional_edges(
        "chat_node",
        route_after_chat,
        {
            "save_memory": "save_memory",
            "rag_agent_wrapper": "rag_agent_wrapper",
            "tools_node": "tools_node",
        },
    )

    workflow.add_edge("rag_agent_wrapper", "chat_node")
    workflow.add_edge("tools_node", "chat_node")
    workflow.add_edge("save_memory", END)

    return workflow.compile(
        name="MainAgent",
        checkpointer=checkpointer,
    )


# =========================================================================== #
# 测试场景
# =========================================================================== #


def print_separator(title: str) -> None:
    """打印分隔标题。"""
    width = 60
    print()
    print("=" * width)
    print(f"  {title}")
    print("=" * width)


def safe_print(text: str) -> None:
    """GBK 安全打印（忽略不可编码字符）。"""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("gbk", errors="replace").decode("gbk"))


def print_messages(state: dict) -> None:
    """打印 state 中最新的 AI 回复。"""
    for m in reversed(state.get("messages", [])):
        if isinstance(m, AIMessage) and m.content:
            text = m.content if isinstance(m.content, str) else str(m.content)
            display = text[:200] + "..." if len(text) > 200 else text
            safe_print(f"  AI -> {display}")
            return


def print_fact(item: Any) -> None:
    """安全打印单条 Store 事实。"""
    v = item.value
    safe_print(f"    - [{v.get('category', '?')}] {v['fact'][:80]}")


def run_prototype() -> None:
    """运行所有原型场景。"""
    # ── 清理旧的 checkpoint 数据库 ──
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print(f"[Setup] 已清理旧数据库: {DB_PATH}")
    else:
        print(f"[Setup] 新数据库: {DB_PATH}")

    # ── 初始化持久化 + Store ──
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    store = InMemoryStore()
    app = build_main_graph(checkpointer, store)

    # ================================================================== #
    # 场景 1: 基础对话 + 记忆提取
    # ================================================================== #
    print_separator("场景 1: 基础对话 + 记忆提取")
    thread_1 = "thread_scene_1"
    config_base = {"configurable": {"thread_id": thread_1}}

    app.invoke(
        {
            "messages": [
                HumanMessage(content="你好，我正在学习 LangGraph 的状态管理机制，想了解 StateGraph 的用法")
            ],
            "user_id": "test_user",
        },
        config_base,
    )
    state_1 = app.get_state(config_base)
    print_messages(state_1.values)

    # 查看 Store 中是否有提取的事实
    facts_1 = list(store.search(("memory", "test_user", "facts")))
    print(f"  Store 事实数: {len(facts_1)}")
    for item in facts_1:
        print_fact(item)

    # 打印 checkpoint 数
    history_1 = list(app.get_state_history(config_base))
    print(f"  Checkpoint 数: {len(history_1)}")

    # ================================================================== #
    # 场景 2: 同一线程继续对话（记忆不变检查）
    # ================================================================== #
    print_separator("场景 2: 同一线程继续对话（累积记忆）")
    app.invoke(
        {
            "messages": [
                HumanMessage(content="我用的是 Python 开发，之前用过 LangChain 的基础功能")
            ],
            "user_id": "test_user",
        },
        config_base,
    )
    state_2 = app.get_state(config_base)
    print_messages(state_2.values)

    facts_2 = list(store.search(("memory", "test_user", "facts")))
    print(f"  Store 事实数（累积）: {len(facts_2)}")
    for item in facts_2:
        print_fact(item)

    # ================================================================== #
    # 场景 3: 跨会话记忆（不同 thread_id，同一 user_id）
    # ================================================================== #
    print_separator("场景 3: 跨会话记忆（新 thread）")
    thread_2 = "thread_scene_3"
    config_new = {"configurable": {"thread_id": thread_2}}

    app.invoke(
        {
            "messages": [
                HumanMessage(content="继续我之前的话题，我想深入了解一下 StateGraph 的节点和边是怎么定义的")
            ],
            "user_id": "test_user",
        },
        config_new,
    )
    state_3 = app.get_state(config_new)
    print_messages(state_3.values)

    # 验证：chat_node 应该从 Store 读到了 test_user 的记忆
    # 并在 system_prompt 尾部加入了 ## Memory 板块
    # 如果 LLM 回复能认出「之前在学」的内容，说明记忆注入生效

    # ================================================================== #
    # 场景 4: 崩溃恢复
    # ================================================================== #
    print_separator("场景 4: 崩溃恢复（get_state_history → resume）")
    thread_3 = "thread_scene_4"
    config_crash = {"configurable": {"thread_id": thread_3}}

    # 发送第一条消息
    app.invoke(
        {
            "messages": [
                HumanMessage(content="Agent 和 LLM 有什么区别？")
            ],
            "user_id": "crash_user",
        },
        config_crash,
    )
    state_4a = app.get_state(config_crash)
    print_messages(state_4a.values)

    # 发送第二条消息（模拟崩溃前已提交的部分对话）
    app.invoke(
        {
            "messages": [
                HumanMessage(content="那 Agent 的记忆机制是怎么实现的？")
            ],
            "user_id": "crash_user",
        },
        config_crash,
    )
    state_4b = app.get_state(config_crash)
    print_messages(state_4b.values)

    # 模拟崩溃：丢弃 app 对象，重新构建
    print("\n  [崩溃] app 对象丢弃，重新从数据库恢复...")
    del app

    app_recovered = build_main_graph(
        SqliteSaver(sqlite3.connect(DB_PATH, check_same_thread=False)),
        store,
    )

    # 通过 get_state_history 查看所有线程
    all_history = list(app_recovered.get_state_history(config_crash))
    print(f"  恢复后历史 checkpoint 数: {len(all_history)}")

    # 查看当前状态（检查 next 是否有未完成的节点）
    current = app_recovered.get_state(config_crash)
    print(f"  当前状态 next: {current.next}")
    print(f"  消息数: {len(current.values.get('messages', []))}")

    # 如果 next 非空（说明有节点未完成），恢复执行
    if current.next:
        print("  [恢复] 存在未完成的步骤，继续执行...")
        app_recovered.invoke(None, config_crash)
        final_state = app_recovered.get_state(config_crash)
        print_messages(final_state.values)

    # ================================================================== #
    # 场景 5: 验证记忆跨恢复生效
    # ================================================================== #
    print_separator("场景 5: 恢复后记忆仍在")
    thread_5 = "thread_scene_5"
    config_5 = {"configurable": {"thread_id": thread_5}}

    # 新用户 asking about Agent memory
    app_recovered.invoke(
        {
            "messages": [
                HumanMessage(content="我刚刚看了 Agent 的记忆机制，还想了解 Tool Calling 是怎么工作的")
            ],
            "user_id": "crash_user",
        },
        config_5,
    )
    state_5 = app_recovered.get_state(config_5)
    print_messages(state_5.values)

    # crash_user 的记忆应该跨会话
    crash_facts = list(store.search(("memory", "crash_user", "facts")))
    print(f"  crash_user Store 事实数: {len(crash_facts)}")
    for item in crash_facts:
        print_fact(item)

    # ================================================================== #
    print("\n== 所有场景完成 ==")


if __name__ == "__main__":
    run_prototype()
