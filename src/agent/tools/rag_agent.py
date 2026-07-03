"""rag_agent 子智能体：Tool 定义 + 包装节点函数。

- ``rag_agent``（``@tool`` 装饰）：供 LLM ``bind_tools`` 的 Tool 对象。
- ``rag_agent_node``：异步 LangGraph 节点函数。直接 ``import`` 模块级预编译的
  RAG 子图实例（``rag_agent.graph.graph``，编译时不带 checkpointer → 运行时
  自动继承父图 checkpointer），用 ``ainvoke`` 调用。

子图作为模块级实例被 wrapper 函数体直接引用，``find_subgraph_pregel`` 的 AST
闭包分析可发现它 → ``PregelNode.subgraphs`` 被填充 → Studio 可展开子图内部节点
（原型 ``phase5_inherit_cp_probe.py`` 验证通过）。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.errors import GraphInterrupt
from pydantic import Field

from agent.debug import dlog, slog
from agent.state import MainState
from rag_agent.graph import graph as rag_graph
from rag_agent.state import Citation


@tool
def rag_agent(
    query: str = Field(
        description="""检索输入。根据 `search_type` 不同，格式和适用场景完全不同：

- **当 `search_type="semantic"` 时（默认，适用于绝大多数问题）**：
  - 使用**完整的自然语言问句**，描述你想要了解的概念或关系。
  - 正确示例：`"StateGraph 中的状态管理机制是怎样的？"`
  - 正确示例：`"如何使用 tool calling 让 Agent 调用外部工具？"`

- **当 `search_type="keyword"` 时（仅限精准实体）**：
  - **使用前提**：用户问题中明确提到了某个**代码符号**（类名、函数名、变量名）、**报错信息**、**配置项名称**，且你需要找到这些符号在文档中**原样出现**的位置。
  - 使用**空格分隔的精准实体**，若实体含空格则用双引号包裹。
  - 正确示例：`"StateGraph"` (查找类名出现的位置)
  - 正确示例：`"tool_calls"` (查找函数调用出现的位置)
  - 正确示例：`"MULTIPLE_SUBGRAPHS"` (查找报错信息)
  - 错误示例：`"StateGraph 状态管理"` (这是概念性描述，而非精准实体，应使用 semantic 模式)"""
    ),
    search_type: str = Field(
        default="semantic",
        description="""检索模式：
        - **`"semantic"` (默认)**：语义向量检索，理解概念和关系。适用于绝大多数问题，包括对某个术语的解释、机制、区别等。
        - **`"keyword"`**：倒排索引精确匹配，**仅当用户想查找某个具体的类名、函数名、报错信息在文档中的位置时使用**。""",
    ),
) -> str:
    """在本地知识库中检索 Agent 开发、LangGraph 框架、面试问题等相关技术内容。

    当用户询问具体技术概念时调用此工具。返回结果包含文件名和行号的引用片段。
    """
    raise RuntimeError(
        "rag_agent tool 不应被 ToolNode 执行——应由 route_after_chat 拦截"
    )


async def rag_agent_node(state: MainState, config: RunnableConfig) -> dict[str, Any]:
    """异步 rag_agent 包装节点。

    直接 ``import`` 模块级预编译的 RAG 子图实例（``rag_graph``，编译时不带
    checkpointer → 运行时自动继承父图 checkpointer），用 ``ainvoke`` 调用。

    子图作为模块级实例在函数体被直接引用，``find_subgraph_pregel`` 的 AST 闭包
    分析可发现它 → ``PregelNode.subgraphs`` 被填充 → Studio 可展开子图内部节点。

    当前 RAG 子图无 ``interrupt()``，但仍保留 ``GraphInterrupt`` 透传（与
    ``resume_agent_node`` 一致，且为未来 RAG 可能加入的 HITL 预留）。
    """
    # rag_graph 为模块级预编译实例（rag_agent.graph.graph），不传 checkpointer
    # → 运行时自动继承父图 checkpointer（None fallthrough 到 configurable）
    dlog("rag", "rag_agent_node", "调用 RAG 子图（模块级实例，继承父图 checkpointer）")
    slog("rag", "rag_agent_node", "进入节点")

    # ── 提取参数并调用 ──
    messages = state.get("messages", [])
    if not messages:
        dlog("rag", "rag_agent_node", "无消息，返回空")
        slog("rag", "rag_agent_node", "无消息，返回空")
        return {"citations": []}

    last_msg = messages[-1]
    tool_calls = getattr(last_msg, "tool_calls", [])
    if not tool_calls:
        dlog("rag", "rag_agent_node", "最后消息无 tool_call，返回空")
        slog("rag", "rag_agent_node", "最后消息无 tool_call，返回空")
        return {"citations": []}

    tool_messages: list[ToolMessage] = []
    all_citations: list[Citation] = []

    for tc in tool_calls:
        args = tc.get("args", {})
        query = args.get("query", args.get("search_query", ""))
        stype = args.get("search_type", "semantic")
        dlog(
            "rag",
            "rag_agent_node",
            "调用 RAG 子图",
            query=query,
            search_type=stype,
            tool_call_id=tc.get("id"),
        )
        slog("rag", "rag_agent_node", "调用 RAG 子图", query=query, search_type=stype)

        # 异步调用子图（模块级实例，继承父图 checkpointer）
        # 当前 RAG 子图无 interrupt，try/except 透传 GraphInterrupt 为预留
        try:
            result = await rag_graph.ainvoke(
                {
                    "search_query": query,
                    "search_type": stype,
                },
                config,
            )
        except GraphInterrupt:
            dlog(
                "rag",
                "rag_agent_node",
                "子图 interrupt，透传 GraphInterrupt（主图将挂起）",
            )
            slog("rag", "rag_agent_node", "子图 interrupt，透传 GraphInterrupt")
            raise

        citations: list[Citation] = result.get("citations_output", [])
        gap_topic: str | None = result.get("gap_topic")
        dlog(
            "rag",
            "rag_agent_node",
            "RAG 子图返回",
            citations_n=len(citations),
            gap_topic=gap_topic,
        )
        slog(
            "rag",
            "rag_agent_node",
            "RAG 子图返回",
            citations_n=len(citations),
            gap_topic=gap_topic,
            files=[c.get("file_path") for c in citations],
        )

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

        tool_messages.append(ToolMessage(content=content, tool_call_id=tc["id"]))
        all_citations.extend(citations)

    return {
        "messages": tool_messages,
        "citations": all_citations,
    }
