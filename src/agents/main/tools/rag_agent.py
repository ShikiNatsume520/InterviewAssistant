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

from agents.rag.graph import graph as rag_graph
from agents.rag.state import Citation
from kernel.logging import dlog


def build_rag_tool_content(
    query: str, citations: list[Citation], gap_topic: str | None
) -> str:
    """构造交给 Main Agent 的单次检索结果与消费约束。"""
    if gap_topic:
        return (
            "[RAG 检索结果]\n"
            "状态：knowledge_gap\n"
            f"知识缺口：{gap_topic}\n\n"
            "[回答要求]\n"
            "本地知识库不足以可靠回答该问题。请明确告知用户当前知识库资料不足，"
            "并询问用户是否允许使用 Research Agent 进行深度搜索。"
            "获得用户明确同意前，不得自行启动深度搜索。"
            "本次回答不得输出 `## 参考资料` 区块。"
        )

    if not citations:
        return (
            "[RAG 检索结果]\n"
            "状态：no_results\n"
            f"检索主题：{query}\n\n"
            "[回答要求]\n"
            "本地知识库没有返回可用资料。请明确告知用户当前知识库资料不足，"
            "并询问用户是否允许使用 Research Agent 进行深度搜索。"
            "获得用户明确同意前，不得自行启动深度搜索。"
            "本次回答不得输出 `## 参考资料` 区块。"
        )

    parts = ["[RAG 检索结果]", "状态：ok", "以下资料仅为候选，不要求全部使用："]
    for index, citation in enumerate(citations, 1):
        parts.append(
            f"[{index}] {citation['file_path']} "
            f"L{citation['start_line']}-{citation['end_line']} "
            f"| 置信度 {citation['score']:.2f}\n"
            f"    {citation['content']}"
        )
    parts.extend(
        (
            "[回答要求]",
            "1. 只采用与用户问题相关且确实支撑回答的候选资料，不要强行使用全部资料。\n"
            "2. 正文实际采用资料时，在对应内容后使用从 `[1]` 开始连续编号的角标。\n"
            "3. 回答末尾必须输出严格的引用区块，格式如下：\n"
            "## 参考资料\n"
            "[1] 原样复制候选资料的文件路径 L起始行-结束行\n"
            "4. 最终引用编号按实际使用顺序重新编号，不必沿用候选编号。\n"
            "5. 只列实际使用的资料；文件路径和行区间必须从候选资料原样复制。\n"
            "6. 引用行不得添加项目符号、置信度或内容摘要；行号连接符必须使用半角 `-`。\n"
            "7. `## 参考资料` 必须是回答最后一个区块，其后不得继续输出。\n"
            "8. 如果最终没有采用任何候选资料，则不要输出 `## 参考资料` 区块。",
        )
    )
    return "\n\n".join(parts)


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


async def rag_agent_node(
    state: dict[str, Any], config: RunnableConfig
) -> dict[str, Any]:
    """异步 rag_agent 包装节点。

    由 ``route_after_chat`` 经 ``Send("rag_agent", {"tool_call": tc})`` 调用，
    从 Send arg 读取本节点负责的单个 tool_call（含 ``query`` / ``search_type``
    / ``id``），调用 RAG 子图，返回 ``ToolMessage``（用 ``tool_call_id`` 构造）
    与结构化 ``citations``。

    直接 ``import`` 模块级预编译的 RAG 子图实例（``rag_graph``，编译时不带
    checkpointer → 运行时自动继承父图 checkpointer），用 ``ainvoke`` 调用。
    子图作为模块级实例在函数体被直接引用，``find_subgraph_pregel`` 的 AST 闭包
    分析可发现它 → ``PregelNode.subgraphs`` 被填充 → Studio 可展开子图内部节点。

    当前 RAG 子图无 ``interrupt()``，但仍保留 ``GraphInterrupt`` 透传（与
    ``resume_agent_node`` 一致，且为未来 RAG 可能加入的 HITL 预留）。
    """
    # rag_graph 为模块级预编译实例（rag_agent.graph.graph），不传 checkpointer
    # → 运行时自动继承父图 checkpointer（None fallthrough 到 configurable）
    dlog("rag", "rag_agent_node", "进入节点")

    tc: dict[str, Any] = state.get("tool_call", {}) or {}
    args = tc.get("args", {}) or {}
    query = str(args.get("query", args.get("search_query", "")))
    stype = str(args.get("search_type", "semantic"))
    tool_call_id = str(tc.get("id", "rag_agent"))

    dlog(
        "rag",
        "rag_agent_node",
        "调用 RAG 子图",
        query=query,
        search_type=stype,
        tool_call_id=tool_call_id,
    )

    # 异步调用子图（模块级实例，继承父图 checkpointer）
    # 当前 RAG 子图无 interrupt，try/except 透传 GraphInterrupt 为预留
    try:
        configurable = config.get("configurable", {})
        principal_id = str(configurable.get("user_id", ""))
        result = await rag_graph.ainvoke(
            {
                "search_query": query,
                "search_type": stype,
                "principal_id": principal_id,
            },
            config,
        )
    except GraphInterrupt:
        dlog(
            "rag", "rag_agent_node", "子图 interrupt，透传 GraphInterrupt（主图将挂起）"
        )
        raise

    citations: list[Citation] = result.get("citations_output", [])
    gap_topic: str | None = result.get("gap_topic")
    dlog(
        "rag",
        "rag_agent_node",
        "RAG 子图返回",
        citations_n=len(citations),
        gap_topic=gap_topic,
        files=[c.get("file_path") for c in citations],
    )

    content = build_rag_tool_content(query, citations, gap_topic)

    update: dict[str, Any] = {
        "messages": [
            ToolMessage(
                content=content,
                tool_call_id=tool_call_id,
                name="rag_agent",
            )
        ],
    }
    # 无候选时不写 citations：同一超级步中另一个并行 RAG 的有效结果不能被清空。
    if citations:
        update["citations"] = citations
    return update
