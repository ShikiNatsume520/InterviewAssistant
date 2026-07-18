"""RAG 子图的 LangGraph 定义（双节点子图）。

节点链：
    retrieve_node（按 search_type 分派管道）→ aggregate_node（gap 判定 + 排序）→ END

编译产物 ``graph`` 注册到 ``langgraph.json``，供 Studio / SDK 调试。
``build_rag_graph(checkpointer=...)`` 供主图以生产模式（带 checkpointer）编译。
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from agents.rag.state import RAGState
from agents.rag.tools.retrieval import aggregate_results, retrieve_pipeline
from kernel.logging import dlog


def retrieve_node(state: RAGState) -> dict[str, Any]:
    """检索节点：按 search_type 分派管道，产出 raw_results。"""
    query = state.get("search_query", "")
    stype = state.get("search_type", "semantic")
    dlog("rag", "retrieve", "检索开始", query=query, search_type=stype)
    results = retrieve_pipeline(query, stype, state.get("principal_id", ""))
    dlog("rag", "retrieve", "检索完成", results_n=len(results))
    return {"raw_results": results}


def aggregate_node(state: RAGState) -> dict[str, Any]:
    """汇总节点：判 gap + 排序，产出 citations_output / gap_topic。"""
    results = state.get("raw_results", [])
    query = state.get("search_query", "")
    citations, gap = aggregate_results(results, query)
    dlog("rag", "aggregate", "汇总完成", citations_n=len(citations), gap_topic=gap)
    return {"citations_output": citations, "gap_topic": gap}


def build_rag_workflow() -> Any:
    """构建未编译的 RAG 子图。

    返回未编译的 ``StateGraph``，由 ``_default_registry`` 在注册阶段编译并绑定 checkpointer。
    """
    workflow = StateGraph(RAGState)
    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("aggregate", aggregate_node)
    workflow.add_edge(START, "retrieve")
    workflow.add_edge("retrieve", "aggregate")
    workflow.add_edge("aggregate", END)
    return workflow


# 供 langgraph.json / SDK 直接发现的模块级实例（无 checkpointer，纯调试用）
graph = build_rag_workflow().compile(name="rag_agent")
"""供 ``langgraph.json`` 及 ``__init__.py`` 导出的模块级图实例。"""
