"""RAG 子图的 LangGraph 定义（双节点子图）。

节点链：
    retrieve_node（按 search_type 分派管道）→ aggregate_node（gap 判定 + 排序）→ END

编译产物 ``graph`` 注册到 ``langgraph.json``，供 Studio / SDK 调试。
不在主图流程中（Phase 3 再通过 wrapper 节点接入）。
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from rag_agent.state import RAGState
from rag_agent.tools.retrieval import aggregate_results, retrieve_pipeline


def retrieve_node(state: RAGState) -> dict:
    """检索节点：按 search_type 分派管道，产出 raw_results。

    Args:
        state: 含 search_query / search_type 的图状态。

    Returns:
        更新 raw_results。
    """
    query = state.get("search_query", "")
    stype = state.get("search_type", "semantic")
    return {"raw_results": retrieve_pipeline(query, stype)}


def aggregate_node(state: RAGState) -> dict:
    """汇总节点：判 gap + 排序，产出 citations_output / gap_topic。

    Args:
        state: 含 raw_results 的图状态。

    Returns:
        更新 citations_output 与 gap_topic。
    """
    results = state.get("raw_results", [])
    query = state.get("search_query", "")
    citations, gap = aggregate_results(results, query)
    return {"citations_output": citations, "gap_topic": gap}


def build_rag_graph() -> StateGraph:
    """构建并编译 RAG 子图。

    Returns:
        编译后的 LangGraph StateGraph 编译产物（可 invoke / astream）。
    """
    g = StateGraph(RAGState)
    g.add_node("retrieve", retrieve_node)
    g.add_node("aggregate", aggregate_node)
    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "aggregate")
    g.add_edge("aggregate", END)
    return g.compile(name="RAGAgent")


graph = build_rag_graph()
"""供 langgraph.json 发现的可执行图实例。"""
