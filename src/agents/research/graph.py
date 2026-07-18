"""Research Agent：计划、逐来源研究、报告展示与知识库授权子图。"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agents.index.service import import_personal_markdown
from agents.research.prompts import (
    build_distill_prompt,
    build_finalize_prompt,
    build_outline_prompt,
)
from agents.research.state import ResearchSource, ResearchState
from agents.research.tools.web_fetch import fetch_text
from agents.research.tools.web_search import connectivity_probe, search
from kernel.config import RESEARCH_MODEL
from kernel.contracts import (
    ConnectivityCheckPayload,
    DecisionInbound,
    OutlineConfirmPayload,
    ResearchKnowledgeConfirmPayload,
    ResearchKnowledgeDecisionInbound,
)
from kernel.llm import get_chat_model
from kernel.logging import dlog


def _parse_json_list(raw: str) -> list[str]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if "```" in raw:
            raw = raw.rsplit("```", 1)[0]
    try:
        parsed = json.loads(raw.strip())
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed if item] if isinstance(parsed, list) else []


def outline_node(state: ResearchState) -> dict[str, Any]:
    """生成 3–5 个检索词；suggest 时参考上次反馈。"""
    gap_topic = state.get("gap_topic", "")
    feedback = state.get("outline_feedback", "")
    feedback_block = (
        f"\n=== 用户对上次大纲的建议（请据此调整）===\n{feedback}\n" if feedback else ""
    )
    response = get_chat_model(RESEARCH_MODEL).invoke(
        build_outline_prompt(gap_topic=gap_topic, feedback_block=feedback_block)
    )
    raw = (
        response.content if isinstance(response.content, str) else str(response.content)
    )
    outline = _parse_json_list(raw) or [gap_topic]
    dlog("research", "outline", "规划完成", outline=outline)
    return {
        "outline": outline,
        "outline_feedback": "",
        "connect_attempts": 0,
        "connectivity_ok": False,
        "query_cursor": 0,
        "source_cursor": 0,
        "sources": [],
        "current_raw_content": "",
        "phase": "waiting_plan",
        "knowledge_decision": "not_asked",
        "import_status": "not_requested",
    }


def outline_confirm_node(state: ResearchState) -> dict[str, Any]:
    """挂起并处理用户对研究计划的批准、调整或拒绝。"""
    value = interrupt(
        OutlineConfirmPayload(
            gap_topic=state.get("gap_topic", ""),
            outline=state.get("outline", []),
        ).model_dump()
    )
    inbound = DecisionInbound.model_validate(value if isinstance(value, dict) else {})
    if inbound.action == "suggest":
        return {
            "outline": [],
            "outline_feedback": inbound.suggestion,
            "phase": "planning",
        }
    if inbound.action == "reject":
        return {"approval_status": "rejected", "phase": "aborted"}
    return {"phase": "checking_network"}


def route_after_outline_confirm(state: ResearchState) -> str:
    """根据计划确认结果选择重规划、研究或终止。"""
    if state.get("approval_status") == "rejected":
        return "abort"
    return "outline" if not state.get("outline") else "connectivity_check"


def connectivity_check_node(state: ResearchState) -> dict[str, Any]:
    """检查搜索服务连通性，并在挂起前持久化失败次数。"""
    if connectivity_probe():
        return {"connectivity_ok": True, "phase": "searching"}
    attempts = state.get("connect_attempts", 0) + 1
    return {
        "connect_attempts": attempts,
        "connectivity_ok": False,
        "_pending_interrupt": ConnectivityCheckPayload(
            attempts=attempts,
            msg=(
                f"无法连接 DuckDuckGo（第 {attempts} 次），请检查网络后继续；"
                "累计 3 次失败将终止深研。"
            ),
        ).model_dump(),
        "phase": "checking_network",
    }


def connectivity_interrupt_node(state: ResearchState) -> dict[str, Any]:
    """等待用户处理网络问题后继续。"""
    interrupt(state.get("_pending_interrupt") or {})
    return {}


def route_after_connectivity(state: ResearchState) -> str:
    """连通时进入检索，否则进入网络确认。"""
    return (
        "discover_query" if state.get("connectivity_ok") else "connectivity_interrupt"
    )


def route_after_connectivity_interrupt(state: ResearchState) -> str:
    """用户继续后重新探测，并在三次失败时终止。"""
    if connectivity_probe():
        return "discover_query"
    return "abort" if state.get("connect_attempts", 0) >= 3 else "connectivity_check"


def _source_id(query: str, url: str) -> str:
    return hashlib.sha256(f"{query}\0{url}".encode()).hexdigest()[:20]


def discover_query_node(state: ResearchState) -> dict[str, Any]:
    """搜索当前关键词并把 URL 固化为稳定来源状态。"""
    cursor = state.get("query_cursor", 0)
    outline = state.get("outline", [])
    if cursor >= len(outline):
        return {"phase": "composing_report"}
    query = outline[cursor]
    existing = list(state.get("sources", []))
    known_ids = {item["source_id"] for item in existing}
    additions: list[ResearchSource] = []
    for result in search(query, max_results=3):
        source_id = _source_id(query, result["href"])
        if source_id in known_ids:
            continue
        additions.append(
            ResearchSource(
                source_id=source_id,
                query=query,
                url=result["href"],
                title=result["title"],
                status="pending",
                failure_reason="",
                note="",
            )
        )
    dlog("research", "discover_query", "发现来源", query=query, count=len(additions))
    return {
        "outline": outline,
        "sources": [*existing, *additions],
        "source_cursor": len(existing),
        "query_cursor": cursor + 1,
        "phase": "searching",
    }


def route_after_discover(state: ResearchState) -> str:
    """发现 URL 后选择处理来源、下一关键词或生成报告。"""
    if state.get("source_cursor", 0) < len(state.get("sources", [])):
        return "prepare_source"
    if state.get("query_cursor", 0) < len(state.get("outline", [])):
        return "discover_query"
    return "compose_report"


def _replace_current_source(
    state: ResearchState, **changes: str
) -> list[ResearchSource]:
    sources = [ResearchSource(**source) for source in state.get("sources", [])]
    cursor = state.get("source_cursor", 0)
    if cursor < len(sources):
        sources[cursor].update(changes)  # type: ignore[typeddict-item]
    return sources


def prepare_source_node(state: ResearchState) -> dict[str, Any]:
    """在耗时抓取前将当前来源标记为抓取中。"""
    return {
        "sources": _replace_current_source(state, status="fetching"),
        "current_raw_content": "",
        "phase": "fetching",
    }


def fetch_source_node(state: ResearchState) -> dict[str, Any]:
    """抓取当前来源正文并记录成功或失败状态。"""
    cursor = state.get("source_cursor", 0)
    sources = state.get("sources", [])
    if cursor >= len(sources):
        return {"current_raw_content": ""}
    source = sources[cursor]
    title, text = fetch_text(source["url"])
    if not text:
        return {
            "sources": _replace_current_source(
                state, status="failed", failure_reason="网页抓取失败或正文为空"
            ),
            "current_raw_content": "",
        }
    return {
        "sources": _replace_current_source(
            state, status="fetched", title=title or source["title"]
        ),
        "current_raw_content": text[:12000],
    }


def route_after_fetch(state: ResearchState) -> str:
    """抓取成功时进入提炼，失败时跳到下一来源。"""
    cursor = state.get("source_cursor", 0)
    sources = state.get("sources", [])
    if cursor >= len(sources) or sources[cursor]["status"] == "failed":
        return "advance_source"
    return "prepare_distill"


def prepare_distill_node(state: ResearchState) -> dict[str, Any]:
    """在 LLM 提炼前将当前来源标记为提炼中。"""
    return {
        "sources": _replace_current_source(state, status="distilling"),
        "phase": "distilling",
    }


def distill_source_node(state: ResearchState) -> dict[str, Any]:
    """把当前网页正文提炼为研究笔记。"""
    cursor = state.get("source_cursor", 0)
    sources = state.get("sources", [])
    if cursor >= len(sources):
        return {"current_raw_content": ""}
    source = sources[cursor]
    prompt = build_distill_prompt(
        query=source["query"],
        title=source["title"],
        content=state.get("current_raw_content", "")[:6000],
    )
    response = get_chat_model(RESEARCH_MODEL).invoke(prompt)
    note = (
        response.content if isinstance(response.content, str) else str(response.content)
    )
    if not note.strip():
        updated = _replace_current_source(
            state, status="failed", failure_reason="网页内容提炼失败"
        )
    else:
        updated = _replace_current_source(state, status="succeeded", note=note.strip())
    return {"sources": updated, "current_raw_content": ""}


def advance_source_node(state: ResearchState) -> dict[str, Any]:
    """推进全局来源游标。"""
    return {"source_cursor": state.get("source_cursor", 0) + 1, "phase": "searching"}


def route_after_advance(state: ResearchState) -> str:
    """选择下一来源、下一关键词或报告生成。"""
    if state.get("source_cursor", 0) < len(state.get("sources", [])):
        return "prepare_source"
    if state.get("query_cursor", 0) < len(state.get("outline", [])):
        return "discover_query"
    return "compose_report"


def _render_notes(sources: list[ResearchSource]) -> str:
    succeeded = [source for source in sources if source["status"] == "succeeded"]
    if not succeeded:
        return "(无有效笔记)"
    return "\n\n".join(
        f"[{index}] 检索词: {source['query']}\n"
        f"    来源: {source['title']} ({source['url']})\n"
        f"    笔记:\n{source['note']}"
        for index, source in enumerate(succeeded, 1)
    )


_SLUG_RE = re.compile(r"[^\w一-鿿]+")


def _proposed_file_name(topic: str) -> str:
    slug = _SLUG_RE.sub("_", topic).strip("_")[:30] or "topic"
    return f"deep_research_{slug}.md"


def prepare_report_node(state: ResearchState) -> dict[str, Any]:
    """在耗时的报告 LLM 调用前发布报告生成状态。"""
    return {
        "outline": state.get("outline", []),
        "query_cursor": state.get("query_cursor", 0),
        "sources": state.get("sources", []),
        "phase": "composing_report",
    }


def compose_report_node(state: ResearchState) -> dict[str, Any]:
    """生成报告但不写文件、不建索引。"""
    topic = state.get("gap_topic", "")
    sources = state.get("sources", [])
    succeeded = [source for source in sources if source["status"] == "succeeded"]
    if succeeded:
        response = get_chat_model(RESEARCH_MODEL).invoke(
            build_finalize_prompt(gap_topic=topic, notes=_render_notes(sources))
        )
        markdown = (
            response.content
            if isinstance(response.content, str)
            else str(response.content)
        )
        markdown = markdown.strip()
        summary = f"已完成「{topic}」的深度研究，共整理 {len(succeeded)} 个有效来源。"
    else:
        markdown = f"# 深研结果：{topic}\n\n本次研究未取得可用网页资料。"
        summary = f"深研「{topic}」未取得可用网页资料。"
    return {
        "gap_topic": topic,
        "outline": state.get("outline", []),
        "query_cursor": state.get("query_cursor", 0),
        "report_markdown": markdown,
        "report_summary": summary,
        "proposed_file_name": _proposed_file_name(topic),
        "knowledge_decision": "pending",
        "import_status": "not_requested",
        "phase": "waiting_knowledge_decision",
    }


def knowledge_confirm_node(state: ResearchState) -> dict[str, Any]:
    """询问是否授权入库，仅记录决定，不执行导入。"""
    succeeded = sum(
        source["status"] == "succeeded" for source in state.get("sources", [])
    )
    value = interrupt(
        ResearchKnowledgeConfirmPayload(
            topic=state.get("gap_topic", ""),
            title=f"深研结果：{state.get('gap_topic', '')}",
            summary=state.get("report_summary", ""),
            source_count=succeeded,
            proposed_file_name=state.get("proposed_file_name", ""),
        ).model_dump()
    )
    inbound = ResearchKnowledgeDecisionInbound.model_validate(
        value if isinstance(value, dict) else {}
    )
    if inbound.action == "approve":
        return {
            "knowledge_decision": "approved",
            "import_status": "pending",
            "approval_status": "approved",
            "phase": "completed",
        }
    return {
        "knowledge_decision": "rejected",
        "import_status": "not_requested",
        "approval_status": "approved",
        "phase": "completed",
    }


async def import_knowledge_node(state: ResearchState) -> dict[str, Any]:
    """通过正式导入服务把已授权报告写入当前 principal 的个人知识库。"""
    principal_id = state.get("principal_id", "")
    if not principal_id:
        return {"import_status": "failed", "phase": "completed"}
    resource = await import_personal_markdown(
        principal_id,
        state.get("proposed_file_name", "深研报告.md"),
        state.get("report_markdown", ""),
        "research",
        idempotency_key=(
            f"research:{state.get('thread_id', '')}:{state.get('tool_call_id', '')}"
        ),
    )
    return {
        "import_status": "completed" if resource.status == "ready" else "failed",
        "knowledge_resource_id": resource.id,
        "phase": "completed",
    }


def route_after_knowledge_confirm(state: ResearchState) -> str:
    """仅在用户批准时进入正式知识导入节点。"""
    return "import_knowledge" if state.get("knowledge_decision") == "approved" else "end"


def abort_node(state: ResearchState) -> dict[str, Any]:
    """统一补齐取消或网络终止状态。"""
    approval = state.get("approval_status") or "aborted"
    return {
        "approval_status": approval,
        "knowledge_decision": "not_asked",
        "import_status": "not_requested",
        "phase": "aborted",
    }


def build_research_workflow() -> Any:
    """构建未绑定独立 checkpointer 的 Research 子图。"""
    workflow = StateGraph(ResearchState)
    workflow.add_node("outline", outline_node)
    workflow.add_node("outline_confirm", outline_confirm_node)
    workflow.add_node("connectivity_check", connectivity_check_node)
    workflow.add_node("connectivity_interrupt", connectivity_interrupt_node)
    workflow.add_node("discover_query", discover_query_node)
    workflow.add_node("prepare_source", prepare_source_node)
    workflow.add_node("fetch_source", fetch_source_node)
    workflow.add_node("prepare_distill", prepare_distill_node)
    workflow.add_node("distill_source", distill_source_node)
    workflow.add_node("advance_source", advance_source_node)
    workflow.add_node("prepare_report", prepare_report_node)
    workflow.add_node("compose_report", compose_report_node)
    workflow.add_node("knowledge_confirm", knowledge_confirm_node)
    workflow.add_node("import_knowledge", import_knowledge_node)
    workflow.add_node("abort", abort_node)
    workflow.add_edge(START, "outline")
    workflow.add_edge("outline", "outline_confirm")
    workflow.add_conditional_edges(
        "outline_confirm",
        route_after_outline_confirm,
        {
            "outline": "outline",
            "connectivity_check": "connectivity_check",
            "abort": "abort",
        },
    )
    workflow.add_conditional_edges(
        "connectivity_check",
        route_after_connectivity,
        {
            "discover_query": "discover_query",
            "connectivity_interrupt": "connectivity_interrupt",
        },
    )
    workflow.add_conditional_edges(
        "connectivity_interrupt",
        route_after_connectivity_interrupt,
        {
            "discover_query": "discover_query",
            "connectivity_check": "connectivity_check",
            "abort": "abort",
        },
    )
    workflow.add_conditional_edges(
        "discover_query",
        route_after_discover,
        {
            "prepare_source": "prepare_source",
            "discover_query": "discover_query",
            "compose_report": "prepare_report",
        },
    )
    workflow.add_edge("prepare_source", "fetch_source")
    workflow.add_conditional_edges(
        "fetch_source",
        route_after_fetch,
        {"prepare_distill": "prepare_distill", "advance_source": "advance_source"},
    )
    workflow.add_edge("prepare_distill", "distill_source")
    workflow.add_edge("distill_source", "advance_source")
    workflow.add_conditional_edges(
        "advance_source",
        route_after_advance,
        {
            "prepare_source": "prepare_source",
            "discover_query": "discover_query",
            "compose_report": "prepare_report",
        },
    )
    workflow.add_edge("prepare_report", "compose_report")
    workflow.add_edge("compose_report", "knowledge_confirm")
    workflow.add_conditional_edges(
        "knowledge_confirm",
        route_after_knowledge_confirm,
        {"import_knowledge": "import_knowledge", "end": END},
    )
    workflow.add_edge("import_knowledge", END)
    workflow.add_edge("abort", END)
    return workflow


graph = build_research_workflow().compile(name="research_agent")
