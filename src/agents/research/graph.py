"""research_agent 子图（自主深研 HITL）。

拓扑::

    START → outline_node (LLM: gap_topic → 3-5 检索词)
          → outline_confirm_node (interrupt: 批准/建议/拒绝大纲)
                  │ approve               │ suggest          │ reject
                  ▼                       ▼                 ▼
          connectivity_check_node   回 outline_node      END (approval_status=rejected)
            (ddgs 探测)
              │ ok                │ fail
              ▼                   ▼
            search_node    interrupt("提示挂梯子")
              │           ┌──────────────────────────────────┐
              ▼           │ POST: connect_attempts+1         │
            finalize_node │  route: attempts<3 回自身         │
              │          │         attempts>=3 → abort       │
              ▼          └──────────────────────────────────┘
            index_rebuild_node (await index_agent.graph.graph.ainvoke)
              │
              ▼
            END (approval_status=approved)

关键机制
--------
- ``outline_confirm_node`` / ``connectivity_check_node`` 用 ``interrupt()`` 挂起。
  wrapper（``research_agent_node``）透传 ``GraphInterrupt``，主图线程暂停，
  下一轮 ``Command(resume=...)`` 精准恢复（原型 phase6_subgraph_interrupt_probe.py
  验证通过）。
- ``connectivity_check_node`` 的重试循环: ``interrupt()`` resume 后节点从头重跑,
  重跑到 ``interrupt()`` 那行返回 resume 值并继续其后代码 —— 故 ``connect_attempts``
  累计写在 ``interrupt()`` 之后的 return 里, 由 ``route_after_connectivity`` 据次数
  路由（3 次失败 → abort）。
- ``index_rebuild_node`` 跨子图调用 Index Agent（后台 agent，不进主图流程）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from agents.research.prompts import (
    build_distill_prompt,
    build_finalize_prompt,
    build_outline_prompt,
)
from agents.research.state import ResearchNote, ResearchState
from agents.research.tools.web_fetch import fetch_text
from agents.research.tools.web_search import connectivity_probe, search
from kernel.config import RESEARCH_MODEL
from kernel.contracts import ConnectivityCheckPayload, OutlineConfirmPayload
from kernel.llm import get_chat_model
from kernel.logging import dlog, slog
from kernel.paths import MARKDOWN_DIR

# --------------------------------------------------------------------------- #
# 全局 LLM（惰性初始化，与 resume_agent.graph 同模式）
# --------------------------------------------------------------------------- #
_outline_llm: Any = None
_distill_llm: Any = None
_finalize_llm: Any = None


def _init_llms() -> None:
    """惰性初始化本子图用到的三组 LLM。"""
    global _outline_llm, _distill_llm, _finalize_llm
    if _outline_llm is None:
        _outline_llm = get_chat_model(RESEARCH_MODEL)
    if _distill_llm is None:
        _distill_llm = get_chat_model(RESEARCH_MODEL)
    if _finalize_llm is None:
        _finalize_llm = get_chat_model(RESEARCH_MODEL)


# --------------------------------------------------------------------------- #
# outline 阶段
# --------------------------------------------------------------------------- #


def _parse_json_list(raw: str) -> list[str]:
    """从 LLM 原始输出解析字符串列表（容错 markdown 代码块包裹）。"""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if "```" in raw:
            raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(s) for s in parsed if s]


def outline_node(state: ResearchState) -> dict[str, Any]:
    """LLM 产出 3-5 个检索词 outline（重规划时参考用户建议）。"""
    _init_llms()
    gap_topic = state.get("gap_topic", "")
    feedback = state.get("outline_feedback", "")
    dlog("research", "outline_node", "进入", gap_topic=gap_topic, feedback=feedback)
    slog("research", "outline_node", "进入", gap_topic=gap_topic)

    feedback_block = (
        f"\n=== 用户对上次大纲的建议（请据此调整）===\n{feedback}\n" if feedback else ""
    )
    resp = _outline_llm.invoke(
        build_outline_prompt(gap_topic=gap_topic, feedback_block=feedback_block)
    )
    raw = resp.content if isinstance(resp.content, str) else str(resp.content)
    slog("research", "outline_node", "LLM 返回", raw_preview=raw[:200])

    outline = _parse_json_list(raw)
    if not outline:
        # 兜底：用 gap_topic 本身做单次检索
        outline = [gap_topic]
    slog("research", "outline_node", "规划完成", outline=outline)
    # 清掉已消费的 feedback，避免下次重规划时残留
    return {
        "outline": outline,
        "outline_feedback": "",
        "connect_attempts": 0,
        "connectivity_ok": False,
    }


def outline_confirm_node(state: ResearchState) -> dict[str, Any]:
    """人机交互：interrupt 等用户批准/建议/拒绝深研大纲。"""
    outline = state.get("outline", [])
    gap_topic = state.get("gap_topic", "")
    dlog(
        "research",
        "outline_confirm_node",
        "interrupt 等待用户确认大纲",
        outline=outline,
    )
    slog(
        "research",
        "outline_confirm_node",
        "interrupt 等待用户确认大纲",
        outline=outline,
    )
    value = interrupt(
        OutlineConfirmPayload(gap_topic=gap_topic, outline=outline).model_dump()
    )
    dlog("research", "outline_confirm_node", "收到用户回复", value=value)
    slog("research", "outline_confirm_node", "收到用户回复", value=value)
    # value: "approve" | {"decision":"suggest","suggestion":"..."} | "reject"
    if isinstance(value, dict) and value.get("decision") == "suggest":
        suggestion = str(value.get("suggestion", ""))
        return {
            "outline": [],  # 触发回 outline_node 重新规划
            "outline_feedback": suggestion,
        }
    if str(value).strip() in ("reject", "拒绝", "拒绝深研"):
        return {"approval_status": "rejected"}
    # approve 或裸字符串批准
    return {}


def route_after_outline_confirm(state: ResearchState) -> str:
    """大纲确认后分派下一步。"""
    if not state.get("outline"):
        dlog("research", "route_after_outline_confirm", "→ outline (重规划)")
        return "outline"
    if state.get("approval_status") == "rejected":
        dlog("research", "route_after_outline_confirm", "→ END (用户拒绝深研)")
        return "abort"
    dlog("research", "route_after_outline_confirm", "→ connectivity_check")
    return "connectivity_check"


# --------------------------------------------------------------------------- #
# 连通性检查阶段（重试循环，原型实测的控制流）
# --------------------------------------------------------------------------- #
def connectivity_check_node(state: ResearchState) -> dict[str, Any]:
    """连通性检查: ddgs 探测; 失败 interrupt 提示挂梯子, resume 后回自身重试, 3 次失败 abort。

    关键机制(原型实测): ``interrupt()`` resume 后节点从头重跑, 重跑到 ``interrupt()`` 那行
    返回 resume 值并继续其后代码。故失败次数累计写在 ``interrupt()`` 之后的 return 里
    (POST-INTERRUPT), 由 ``route_after_connectivity`` 据次数路由。
    """
    attempts = state.get("connect_attempts", 0)
    dlog("research", "connectivity_check_node", "ENTER", attempts=attempts)
    slog("research", "connectivity_check_node", "检查连通性", attempts=attempts)

    if connectivity_probe():
        dlog("research", "connectivity_check_node", "连通 OK → search")
        slog("research", "connectivity_check_node", "连通 OK")
        return {"connectivity_ok": True}

    new_attempts = attempts + 1
    dlog("research", "connectivity_check_node", f"第 {new_attempts} 次失败 → interrupt")
    slog(
        "research",
        "connectivity_check_node",
        "连通失败, interrupt 提示挂梯子",
        attempts=new_attempts,
    )
    interrupt(
        ConnectivityCheckPayload(
            attempts=new_attempts,
            msg=(
                f"无法连接 DuckDuckGo（第 {new_attempts} 次），请挂梯子后回复任意"
                "内容继续；累计 3 次失败将终止深研。"
            ),
        ).model_dump()
    )
    # POST-INTERRUPT: resume 后从这里继续, 把失败次数写回 state 供条件边路由
    dlog(
        "research",
        "connectivity_check_node",
        "POST-INTERRUPT, 写回 attempts",
        new_attempts=new_attempts,
    )
    return {"connect_attempts": new_attempts, "connectivity_ok": False}


def route_after_connectivity(state: ResearchState) -> str:
    """连通性检查后: ok → search; 失败且 attempts>=3 → abort; 否则回自身重试。"""
    if state.get("connectivity_ok"):
        dlog("research", "route_after_connectivity", "→ search")
        return "search"
    attempts = state.get("connect_attempts", 0)
    if attempts >= 3:
        dlog("research", "route_after_connectivity", "3 次失败 → abort")
        slog(
            "research",
            "route_after_connectivity",
            "3 次失败 → abort",
            attempts=attempts,
        )
        return "abort"
    dlog(
        "research",
        "route_after_connectivity",
        f"回 connectivity_check 重试 (attempts={attempts})",
    )
    return "connectivity_check"


# --------------------------------------------------------------------------- #
# 搜索阶段（确定性按 outline 逐词执行）
# --------------------------------------------------------------------------- #


def _distill(query: str, title: str, content: str) -> str:
    """LLM 从单页正文提炼与查询相关的结构化笔记。"""
    prompt = build_distill_prompt(query=query, title=title, content=content[:6000])
    resp = _distill_llm.invoke(prompt)
    raw = resp.content if isinstance(resp.content, str) else str(resp.content)
    return raw.strip()


def search_node(state: ResearchState) -> dict[str, Any]:
    """遍历 outline 逐词: ddgs top-3 URL → 爬正文 → LLM 提炼笔记 → 累积。"""
    _init_llms()
    outline = state.get("outline", [])
    dlog("research", "search_node", "开始搜索", outline=outline)
    slog("research", "search_node", "开始搜索", outline_n=len(outline))

    notes: list[ResearchNote] = []
    for query in outline:
        results = search(query, max_results=3)
        slog(
            "research", "search_node", "ddgs 结果", query=query, results_n=len(results)
        )
        for r in results:
            title, text = fetch_text(r["href"])
            if not text:
                dlog(
                    "research", "search_node", "爬取失败/正文为空, 跳过", url=r["href"]
                )
                continue
            distilled = _distill(query, title or r["title"], text)
            if not distilled:
                continue
            notes.append(
                ResearchNote(
                    query=query,
                    source_title=title or r["title"],
                    source_url=r["href"],
                    content=distilled,
                )
            )
            slog(
                "research",
                "search_node",
                "提炼笔记",
                query=query,
                url=r["href"],
                note_len=len(distilled),
            )

    dlog("research", "search_node", "搜索完成", notes_n=len(notes))
    slog("research", "search_node", "搜索完成", notes_n=len(notes))
    return {"research_notes": notes}


# --------------------------------------------------------------------------- #
# finalize 阶段（写新 Markdown）
# --------------------------------------------------------------------------- #
_SLUG_RE = re.compile(r"[^\w一-鿿]+")
"""slug 化用: 保留字母数字与中日韩字符, 其余转 _。"""


def _slugify(text: str, max_len: int = 30) -> str:
    """把 gap_topic 转为文件名友好的 slug。"""
    slug = _SLUG_RE.sub("_", text).strip("_")
    return slug[:max_len] or "topic"


def _new_file_name(gap_topic: str, ts: str) -> str:
    """生成新 markdown 文件名: deep_research_<slug>_<ts>.md。"""
    return f"deep_research_{_slugify(gap_topic)}_{ts}.md"


def _render_notes(notes: list[ResearchNote]) -> str:
    """把笔记列表渲染成 finalize prompt 用的文本。"""
    if not notes:
        return "(无笔记)"
    parts: list[str] = []
    for i, n in enumerate(notes, 1):
        parts.append(
            f"[{i}] 检索词: {n['query']}\n    来源: {n['source_title']} ({n['source_url']})\n"
            f"    笔记:\n{n['content']}"
        )
    return "\n\n".join(parts)


def finalize_node(state: ResearchState) -> dict[str, Any]:
    """LLM 整理笔记成结构化 Markdown, 写入 data/markdown/。"""
    _init_llms()
    gap_topic = state.get("gap_topic", "")
    notes = state.get("research_notes", [])
    dlog(
        "research", "finalize_node", "整理笔记", notes_n=len(notes), gap_topic=gap_topic
    )
    slog("research", "finalize_node", "整理笔记", notes_n=len(notes))

    if not notes:
        # 无有效笔记: 仍写一个最小记录文件以便索引重建（也可直接 abort, 但写文件更可追溯）
        markdown = f"# 深研资料: {gap_topic}\n\n> 深研未取得有效资料。\n"
        summary = f"深研「{gap_topic}」未搜集到有效资料。"
    else:
        resp = _finalize_llm.invoke(
            build_finalize_prompt(gap_topic=gap_topic, notes=_render_notes(notes))
        )
        markdown = resp.content if isinstance(resp.content, str) else str(resp.content)
        markdown = markdown.strip()
        summary = f"已通过深研补足「{gap_topic}」，整理为 {len(notes)} 条来源的结构化资料入库。"

    # 时间戳在 finalize 写文件时生成（运行时 datetime 可用）
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    file_name = _new_file_name(gap_topic, ts)
    md_path = MARKDOWN_DIR / file_name
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(markdown, encoding="utf-8")
    dlog("research", "finalize_node", "已写入文件", path=str(md_path))
    slog(
        "research",
        "finalize_node",
        "已写入文件",
        file_name=file_name,
        md_len=len(markdown),
    )

    return {
        "new_file_name": file_name,
        "approval_status": "approved",
        "summary": summary,
    }


# --------------------------------------------------------------------------- #
# 索引重建阶段（跨子图调用 Index Agent）
# --------------------------------------------------------------------------- #
async def index_rebuild_node(state: ResearchState) -> dict[str, Any]:
    """跨子图调用 Index Agent 重建索引（target_files = 新文件名）。"""
    new_file = state.get("new_file_name", "")
    dlog("research", "index_rebuild_node", "调用 index_agent", target_files=[new_file])
    slog("research", "index_rebuild_node", "调用 index_agent", target_files=[new_file])

    if not new_file:
        slog("research", "index_rebuild_node", "无新文件名, 跳过")
        return {}

    from agents.index.graph import graph as index_graph

    result = await index_graph.ainvoke({"target_files": [new_file]})
    n_chunks = len(result.get("chunks", []))
    dlog("research", "index_rebuild_node", "index_agent 完成", n_chunks=n_chunks)
    slog("research", "index_rebuild_node", "index_agent 完成", n_chunks=n_chunks)
    return {}


# --------------------------------------------------------------------------- #
# abort 节点（统一终止出口）
# --------------------------------------------------------------------------- #
def abort_node(state: ResearchState) -> dict[str, Any]:
    """统一 abort 出口: 设置 approval_status=rejected/aborted, 退出子图。

    进 abort_node 有两种情况: 用户拒绝大纲(rejected 已在 outline_confirm 设置),
    或连通性 3 次失败(aborted)。此节点仅补齐未设置的 approval_status。
    """
    if not state.get("approval_status"):
        dlog("research", "abort_node", "连通性 3 次失败 → aborted")
        slog("research", "abort_node", "连通性 3 次失败 → aborted")
        return {"approval_status": "aborted"}
    dlog("research", "abort_node", "用户拒绝深研 → rejected")
    return {}


# --------------------------------------------------------------------------- #
# 子图构建
# --------------------------------------------------------------------------- #
def build_research_workflow() -> Any:
    """构建未编译的 research 子图。

    返回未编译的 ``StateGraph``, 模块级 ``graph`` 实例编译时不带 checkpointer,
    运行时自动继承父图 checkpointer（与 rag_agent / resume_agent 一致）。
    """
    workflow = StateGraph(ResearchState)
    workflow.add_node("outline", outline_node)
    workflow.add_node("outline_confirm", outline_confirm_node)
    workflow.add_node("connectivity_check", connectivity_check_node)
    workflow.add_node("search", search_node)
    workflow.add_node("finalize", finalize_node)
    workflow.add_node("index_rebuild", index_rebuild_node)
    workflow.add_node("abort", abort_node)

    workflow.set_entry_point("outline")
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
            "search": "search",
            "connectivity_check": "connectivity_check",
            "abort": "abort",
        },
    )
    workflow.add_edge("search", "finalize")
    workflow.add_edge("finalize", "index_rebuild")
    workflow.add_edge("index_rebuild", END)
    workflow.add_edge("abort", END)
    return workflow


# 供 langgraph.json / SDK 直接发现的模块级实例（无 checkpointer，纯调试用）
graph = build_research_workflow().compile(name="research_agent")
"""供 ``langgraph.json`` 及 ``__init__.py`` 导出的模块级图实例。"""
