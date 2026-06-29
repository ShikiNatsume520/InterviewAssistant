"""rag_agent 子智能体：Tool 定义 + 包装节点函数。

- ``rag_agent``（``@tool`` 装饰）：供 LLM ``bind_tools`` 的 Tool 对象，
  仅用于 Schema 生成，实际执行由路由拦截。
- ``rag_agent_node``：LangGraph 节点函数，从 ``tool_call.args`` 提取参数 →
  invoke ``rag_subgraph`` → 返回 ``ToolMessage`` + ``citations``。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from pydantic import Field

from agent.state import MainState
from rag_agent.graph import graph as rag_subgraph
from rag_agent.state import Citation


@tool
def rag_agent(
    query: str = Field(
        description="""检索输入。根据 `search_type` 不同，格式和适用场景完全不同：

- **当 `search_type="semantic"` 时（默认，适用于绝大多数问题）**：
  - 使用**完整的自然语言问句**，描述你想要了解的概念或关系。
  - ✅ 正确示例：`"StateGraph 中的状态管理机制是怎样的？"`
  - ✅ 正确示例：`"如何使用 tool calling 让 Agent 调用外部工具？"`

- **当 `search_type="keyword"` 时（仅限精准实体）**：
  - **使用前提**：用户问题中明确提到了某个**代码符号**（类名、函数名、变量名）、**报错信息**、**配置项名称**，且你需要找到这些符号在文档中**原样出现**的位置。
  - 使用**空格分隔的精准实体**，若实体含空格则用双引号 `" "` 包裹。
  - ✅ 正确示例：`"StateGraph"` (查找类名出现的位置)
  - ✅ 正确示例：`"tool_calls"` (查找函数调用出现的位置)
  - ✅ 正确示例：`"MULTIPLE_SUBGRAPHS"` (查找报错信息)
  - ❌ 错误示例：`"StateGraph 状态管理"` (这是概念性描述，而非精准实体，应使用 semantic 模式)"""
    ),
    search_type: str = Field(
        default="semantic",
        description="""检索模式：
        - **`"semantic"` (默认)**：语义向量检索，理解概念和关系。适用于绝大多数问题，包括对某个术语的解释、机制、区别等。
        - **`"keyword"`**：倒排索引精确匹配，**仅当用户想查找某个具体的类名、函数名、报错信息在文档中的位置时使用**。"""
    )
) -> str:
    """在本地知识库中检索 Agent 开发、LangGraph 框架、面试问题等相关技术内容。
    当用户询问具体技术概念时调用此工具。返回结果包含文件名和行号的引用片段。
    """
    # 此工具仅用于 LLM 工具绑定 schema，实际执行由路由拦截后经
    # rag_agent_node 包装节点处理。
    raise RuntimeError(
        "rag_agent tool 不应被 ToolNode 执行——应由 route_after_chat 拦截"
    )


def rag_agent_node(state: MainState) -> dict[str, Any]:
    """rag_agent 包装节点。

    从 ``state.messages[-1].tool_calls[0].args`` 提取参数 →
    invoke ``rag_subgraph`` → ``ToolMessage`` + ``citations``。
    """
    messages = state.get("messages", [])
    if not messages:
        return {"citations": []}

    last_msg = messages[-1]
    tool_calls = getattr(last_msg, "tool_calls", [])
    if not tool_calls:
        return {"citations": []}

    tool_call = tool_calls[0]
    args = tool_call.get("args", {})

    # 映射 LLM 参数 → RAGState 输入字段
    result = rag_subgraph.invoke(  # type: ignore[attr-defined]
        {
            "search_query": args.get("query", args.get("search_query", "")),
            "search_type": args.get("search_type", "semantic"),
        }
    )

    citations: list[Citation] = result.get("citations_output", [])
    gap_topic: str | None = result.get("gap_topic")

    # 构造 ToolMessage 内容
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
        "messages": [
            ToolMessage(
                content=content,
                tool_call_id=tool_call["id"],
            )
        ],
        "citations": citations,
    }
