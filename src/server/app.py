"""FastAPI 端到端服务（后端驱动 + checkpoint 恢复范式）。

端点
----
- ``POST /v1/chat``：前端发送按钮 POST，``graph.astream(input, config)`` 跑一轮
  （有 pending interrupt→Command resume，否则新 HumanMessage）。
- ``POST /v1/checkpoint``：前端 init 末尾 POST，``astream(None, config)`` 从最近
  checkpoint 续跑（刷新恢复 / 卡在节点间自动续跑）。
- ``GET /v1/state?thread=``：解析最近 checkpoint，返回前端 init 所需路由+渲染数据
  （停在哪个图 / pending interrupt / 主图+子图 messages 快照）。
- ``GET /v1/thread``：返回当前会话 thread_id（主页 init 第一步）。

SSE 事件（/v1/chat、/v1/checkpoint）：
- ``token``：``{ns:"main"|"resume_agent", text}``，subgraphs=True 捕获主图+嵌套子图
  LLM token，按 namespace 归一化。
- ``interrupt``：图挂起，data 为 interrupt payload（phase+字段）。
- ``done``：本轮结束无 interrupt，data 含 last_message + current_resume + citations。

刷新恢复：前端断开 SSE → async generator cancel → astream 在下个可取消点停
（节点间间隙或当前节点完成后）。最近 checkpoint 已存最近完成节点，前端 init 经
GET /v1/state 拿到，POST /v1/checkpoint 从 checkpoint 续跑。LLM 节点同步 invoke
不可中断，保证其完成存档（约束：LLM 节点保持同步 invoke）。

启动
----
    python -m uvicorn server.app:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import BaseModel, Field, ValidationError
from sse_starlette.sse import EventSourceResponse

from agents.main.graph import build_main_graph
from kernel.contracts import (
    PHASE_TO_INBOUND,
    ConnectivityCheckPayload,
    OutlineConfirmPayload,
    PlanConfirmPayload,
    ResumeApprovePayload,
    ResumeHitlPayload,
    ResumeSelectPayload,
    normalize_resume_value,
)
from kernel.logging import dlog
from kernel.paths import PROJECT_ROOT
from kernel.persistence import get_store

# --------------------------------------------------------------------------- #
# 持久化文件
# --------------------------------------------------------------------------- #

CHECKPOINT_DB = "sqlite_checkpoints.db"
"""主图断点数据库（与 langgraph dev 的 checkpointer.py 一致路径）。"""


# --------------------------------------------------------------------------- #
# 应用生命周期：持有 AsyncSqliteSaver + 编译后的主图单例
# --------------------------------------------------------------------------- #

_state: dict[str, Any] = {}
"""进程级单例容器：``{"saver": AsyncSqliteSaver, "graph": CompiledStateGraph}``."""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用启动/关闭钩子：初始化 saver + 编译主图。"""
    store = get_store()
    async with AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB) as saver:
        graph = build_main_graph(checkpointer=saver, store=store)
        _state["saver"] = saver
        _state["graph"] = graph
        yield
    _state.clear()


app = FastAPI(title="InterviewAssistant", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# 请求 / 响应模型
# --------------------------------------------------------------------------- #


class ChatRequest(BaseModel):
    """``/v1/chat`` 请求体（前端发送按钮 POST）。"""

    user_id: str = Field(
        default="default", description="用户标识，用于长期记忆 Store 键控"
    )
    thread_id: str = Field(..., description="会话线程标识，用于 checkpoint 回放")
    message: str = Field(
        default="", description="本轮用户消息内容（恢复 interrupt / 续跑时可空）"
    )
    resume_value: Any | None = Field(
        default=None,
        description=(
            "恢复 interrupt 时传给子图的值。若提供则优先于 message——"
            "建议场景传 {decision: suggest, suggestion: ...}，"
            "批准/拒绝场景传 approve / reject。"
        ),
    )


class CheckpointRequest(BaseModel):
    """``/v1/checkpoint`` 请求体（前端 init 末尾 POST，从最近 checkpoint 续跑）。"""

    thread_id: str = Field(..., description="会话线程标识")
    user_id: str = Field(default="default", description="用户标识（与原会话一致）")


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #

_PHASE_TO_SCHEMA: dict[str, type[BaseModel]] = {
    "plan_confirm": PlanConfirmPayload,
    "outline_confirm": OutlineConfirmPayload,
    "connectivity_check": ConnectivityCheckPayload,
    "resume_select": ResumeSelectPayload,
    "resume_approve": ResumeApprovePayload,
    "resume_hitl": ResumeHitlPayload,
}
"""interrupt payload phase → contracts schema 映射（``_interrupt_payload`` 校验用）。"""


async def _pending_interrupt(graph: Any, config: dict[str, Any]) -> tuple[bool, Any]:
    """检测当前 thread 是否有 pending interrupt。"""
    state = await graph.aget_state(config)
    tasks = getattr(state, "tasks", []) or []
    for t in tasks:
        intr = getattr(t, "interrupts", None) or []
        if intr:
            return True, intr
    return False, None


def _pending_phase(intrs: Any) -> str | None:
    """从 pending interrupt 列表取第一个 interrupt 的 ``phase``。"""
    for i in intrs or []:
        val = getattr(i, "value", i)
        if isinstance(val, dict) and "phase" in val:
            return str(val["phase"])
    return None


def _normalize_and_validate(phase: str | None, value: Any) -> Any:
    """归一化 resume 值并校验，返回规范 dict（传给 ``Command(resume=...)``）。

    旧前端裸串 / 旧 dict 经 ``normalize_resume_value`` 转成规范 dict，再用
    ``PHASE_TO_INBOUND[phase]`` schema 校验。校验失败或无 phase 时不阻断——
    记日志并透传归一化结果（保证旧前端兼容，不因新校验而崩）。
    """
    if phase is None:
        return value
    normalized = normalize_resume_value(phase, value)
    schema = PHASE_TO_INBOUND.get(phase)
    if schema is None:
        return normalized
    try:
        return schema.model_validate(normalized).model_dump()
    except ValidationError as e:
        dlog(
            "server",
            "_normalize_and_validate",
            "校验失败，透传归一化结果",
            phase=phase,
            err=str(e).splitlines()[0],
        )
        return normalized


def _interrupt_payload(intrs: Any) -> dict[str, Any]:
    """把 interrupt 对象列表转成可序列化的 dict。

    若 interrupt value 是含 ``phase`` 的 dict，用 ``kernel.contracts`` 对应 schema
    校验后序列化（确保字段形状）；否则原样透传。
    """
    out: list[Any] = []
    for i in intrs:
        val = getattr(i, "value", i)
        if isinstance(val, dict) and "phase" in val:
            schema = _PHASE_TO_SCHEMA.get(val["phase"])
            if schema is not None:
                val = schema.model_validate(val).model_dump()
        out.append(val if isinstance(val, (str, dict, list)) else str(val))
    return {"interrupts": out}


# --------------------------------------------------------------------------- #
# 端点
# --------------------------------------------------------------------------- #


async def _stream_graph(
    graph: Any, input_data: Any, config: dict[str, Any]
) -> AsyncIterator[dict[str, str]]:
    """共享流式生成器：跑图 + 推 SSE（token 带 ns / interrupt / done）。

    供 ``/v1/chat``（带 input）与 ``/v1/checkpoint``（``None`` 从最近 checkpoint 续跑）
    复用。跑完查 interrupt：有→推 interrupt，无→推 done（含 current_resume）。
    """
    # token 级流式（subgraphs=True 捕获主图 + 嵌套子图 token）
    async for ns_tuple, payload in graph.astream(
        input_data,
        config,
        stream_mode="messages",
        subgraphs=True,
    ):
        # subgraphs=True 时事件为 (namespace_tuple, (chunk, metadata))
        ns = _normalize_ns(ns_tuple)
        chunk = (
            payload[0] if isinstance(payload, tuple) and len(payload) >= 1 else payload
        )
        text = ""
        if isinstance(chunk, str):
            text = chunk
        else:
            content = getattr(chunk, "content", "")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text += str(part.get("text", ""))
        if text:
            yield {
                "event": "token",
                "data": json.dumps({"ns": ns, "text": text}, ensure_ascii=False),
            }

    # 流结束后查 interrupt / done
    pending2, intrs2 = await _pending_interrupt(graph, config)
    dlog("server", "_stream_graph", f"流结束，pending_interrupt={pending2}")
    if pending2:
        yield {
            "event": "interrupt",
            "data": json.dumps(_interrupt_payload(intrs2), ensure_ascii=False),
        }
    else:
        state = await graph.aget_state(config)
        msgs = state.values.get("messages", [])
        last_ai = ""
        if msgs:
            last = msgs[-1]
            if getattr(last, "type", "") == "ai":
                c = getattr(last, "content", "")
                last_ai = c if isinstance(c, str) else str(c)
        citations = state.values.get("citations", []) or []
        current_resume = state.values.get("current_resume", "") or ""
        dlog(
            "server",
            "_stream_graph",
            "done",
            last_msg_len=len(last_ai),
            citations_n=len(citations),
            resume_len=len(current_resume),
        )
        yield {
            "event": "done",
            "data": json.dumps(
                {
                    "last_message": last_ai[:500],
                    "current_resume": current_resume,
                    "citations": citations,
                },
                ensure_ascii=False,
                default=str,
            ),
        }


@app.post("/v1/chat")
async def chat(req: ChatRequest) -> EventSourceResponse:
    """流式对话端点（前端发送按钮 POST）。

    SSE 事件：``token``（``{ns, text}``，ns=main/resume_agent）/ ``interrupt`` /
    ``done``（含 current_resume）。
    """
    graph = _state["graph"]
    config: dict[str, Any] = {"configurable": {"thread_id": req.thread_id}}
    dlog(
        "server",
        "/v1/chat",
        "收到请求",
        user_id=req.user_id,
        thread_id=req.thread_id,
        message_len=len(req.message),
        has_resume_value=req.resume_value is not None,
    )

    async def event_gen() -> AsyncIterator[dict[str, str]]:
        pending, intrs = await _pending_interrupt(graph, config)
        dlog("server", "/v1/chat", f"pending_interrupt={pending}")
        if pending:
            # 有 pending interrupt：用 resume_value（或 message）恢复。
            # 不在开头推 interrupt——前端在上一轮流末已收到并渲染了该 interrupt，
            # 这里再推会重复弹框。图恢复后跑到下个挂起点由 _stream_graph 流末推。
            raw_value: Any = (
                req.resume_value if req.resume_value is not None else req.message
            )
            # 归一化：旧前端裸串/旧 dict → 规范 dict {action, ...}，再 schema 校验。
            # 旧 static/ 前端与新 React 前端在此对齐，子图节点只读规范字段。
            phase = _pending_phase(intrs)
            value: Any = _normalize_and_validate(phase, raw_value)
            input_data: Any = Command(resume=value)
            dlog(
                "server",
                "/v1/chat",
                "用 Command(resume=...) 恢复",
                phase=phase,
                value_type=type(value).__name__,
            )
        else:
            # 无 pending：新 HumanMessage 启动一轮（message 可为空——空时若图无
            # checkpoint 会立即 END，若有 checkpoint 则 astre 等同于续跑，但调用方
            # 续跑应走 /v1/checkpoint；这里 message 空属异常调用，仍按空 message 启动）
            input_data = {
                "messages": [HumanMessage(content=req.message)],
                "user_id": req.user_id,
            }
            dlog("server", "/v1/chat", "用新 HumanMessage 启动一轮")

        async for ev in _stream_graph(graph, input_data, config):
            yield ev

    return EventSourceResponse(event_gen())


@app.post("/v1/checkpoint")
async def checkpoint(req: CheckpointRequest) -> EventSourceResponse:
    """从最近 checkpoint 续跑端点（前端 init 末尾 POST）。

    ``invoke(None, config)`` 让 LangGraph 自动加载最近 checkpoint 继续执行下一
    超级步。用于刷新恢复 / 页面加载后自动续跑卡在节点间的执行。
    """
    graph = _state["graph"]
    config: dict[str, Any] = {"configurable": {"thread_id": req.thread_id}}
    dlog("server", "/v1/checkpoint", "收到请求", thread_id=req.thread_id)

    async def event_gen() -> AsyncIterator[dict[str, str]]:
        state = await graph.aget_state(config)
        next_nodes = list(getattr(state, "next", []) or [])
        dlog(
            "server",
            "/v1/checkpoint",
            f"next={next_nodes}, tasks_n={len(getattr(state, 'tasks', []) or [])}",
        )
        if not next_nodes:
            # 已 END 或无 checkpoint——直接推当前 interrupt/done
            pending, intrs = await _pending_interrupt(graph, config)
            if pending:
                yield {
                    "event": "interrupt",
                    "data": json.dumps(_interrupt_payload(intrs), ensure_ascii=False),
                }
            else:
                yield {
                    "event": "done",
                    "data": json.dumps(
                        {"last_message": "", "current_resume": "", "citations": []}
                    ),
                }
            return
        # invoke(None) 从最近 checkpoint 续跑
        async for ev in _stream_graph(graph, None, config):
            yield ev

    return EventSourceResponse(event_gen())


def _normalize_ns(ns_tuple: Any) -> str:
    """把 LangGraph 子图 namespace tuple 归一化为前端可辨识的来源标签。

    - 空 tuple（主图）→ ``"main"``
    - 非空且首个元素以 ``resume_agent`` 开头（形如 ``resume_agent:<命名空间ID>``）
      → ``"resume_agent"``
    - 其他子图 → 首元素原样（保留便于扩展）
    """
    if not ns_tuple:
        return "main"
    first = str(ns_tuple[0])
    if first.startswith("resume_agent"):
        return "resume_agent"
    return first


def _serialize_messages(msgs: list[Any]) -> list[dict[str, Any]]:
    """把 BaseMessage 列表序列化为前端可渲染的 dict 列表。"""
    out: list[dict[str, Any]] = []
    for m in msgs:
        mtype = getattr(m, "type", type(m).__name__)
        content = getattr(m, "content", "")
        content = content if isinstance(content, str) else str(content)
        entry: dict[str, Any] = {"type": mtype, "content": content}
        tcs = getattr(m, "tool_calls", None)
        if tcs:
            entry["tool_calls"] = [
                {"name": str(tc.get("name", "")), "args": tc.get("args", {})}
                for tc in tcs
            ]
        out.append(entry)
    return out


def _extract_subgraph_state(state: Any) -> dict[str, Any] | None:
    """从主图 state.tasks[].state 挖 resume 子图 state 摘要。

    主图挂起在 resume_agent 节点时，task 带 state（子图 state）。LangGraph 版本差异
    下 ``task.state`` 可能是 dict（直接 state 值）或 StateSnapshot（取 ``.values``）。
    兼容两种。判断是否 resume 子图：state 含 ``resume_shot`` / ``resume_file``。挖出供
    /resume 前端恢复左栏 shot + 右栏 messages。非 resume 会话返 None。
    """
    for t in getattr(state, "tasks", []) or []:
        tstate = getattr(t, "state", None)
        if tstate is None:
            continue
        # task.state 可能是 dict（直接 values）或 StateSnapshot（取 .values）
        if isinstance(tstate, dict):
            vals: dict[str, Any] = tstate
        else:
            v = getattr(tstate, "values", None)
            vals = v if isinstance(v, dict) else {}
        dlog(
            "server",
            "_extract_subgraph_state",
            "task",
            name=str(getattr(t, "name", "")),
            tstate_type=type(tstate).__name__,
            vals_keys=list(vals.keys()) if isinstance(vals, dict) else None,
        )
        if "resume_shot" in vals or "resume_file" in vals:
            return {
                "intent": str(vals.get("intent", "")),
                "resume_shot": str(vals.get("resume_shot", "")),
                "resume_file": str(vals.get("resume_file", "")),
                "messages": _serialize_messages(list(vals.get("messages", []))),
                "last_summary": str(vals.get("last_summary", "")),
                "plan": list(vals.get("plan", []) or []),
            }
    return None


@app.get("/v1/state")
async def get_state(thread_id: str) -> dict[str, Any]:
    """解析最近 checkpoint，返回前端 init 所需路由 + 渲染数据。

    返回：
    - ``next``：主图待执行节点（空=END）
    - ``pending_interrupt``：当前挂起 interrupt payload（无则 null）
    - ``in_resume``：是否在 resume 子图（next 含 resume_agent 或子图 state 存在）
    - ``main``：主图 messages / current_resume / citations
    - ``resume``：resume 子图 state 摘要（非 resume 会话为 null）
    """
    graph = _state["graph"]
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    state = await graph.aget_state(config)
    next_nodes = list(getattr(state, "next", []) or [])
    vals = state.values or {}

    pending, intrs = await _pending_interrupt(graph, config)
    interrupt_payload = _interrupt_payload(intrs) if pending else None
    # pending_interrupt 取第一个 interrupt 的 value（已 schema 校验）
    first_interrupt: dict[str, Any] | None = None
    if interrupt_payload and isinstance(interrupt_payload.get("interrupts"), list):
        intrs_list = interrupt_payload["interrupts"]
        if intrs_list:
            first_interrupt = intrs_list[0] if isinstance(intrs_list[0], dict) else None

    resume_snap = _extract_subgraph_state(state)
    in_resume = bool(resume_snap) or any("resume_agent" in str(n) for n in next_nodes)

    dlog(
        "server",
        "/v1/state",
        "解析 checkpoint",
        next_nodes=next_nodes,
        in_resume=in_resume,
        has_interrupt=bool(first_interrupt),
        resume_msgs_n=len(resume_snap["messages"]) if resume_snap else 0,
    )

    return {
        "thread_id": thread_id,
        "next": next_nodes,
        "pending_interrupt": first_interrupt,
        "in_resume": in_resume,
        "main": {
            "messages": _serialize_messages(list(vals.get("messages", []))),
            "current_resume": str(vals.get("current_resume", "") or ""),
            "citations": list(vals.get("citations", []) or []),
        },
        "resume": resume_snap,
    }


@app.get("/v1/thread")
async def get_thread() -> dict[str, str]:
    """返回当前会话 thread_id（主页 init 第一步）。

    简单实现：单会话，thread_id 存进程内存（重启丢失，符合"暂时简单实现"）。
    首次无会话时自动生成。多会话预留后续扩展。
    """
    tid = _state.get("current_thread_id")
    if not tid:
        import secrets

        tid = "web-" + secrets.token_hex(6)
        _state["current_thread_id"] = tid
        dlog("server", "/v1/thread", "生成新 thread_id", tid=tid)
    return {"thread_id": tid}


@app.get("/health")
async def health() -> dict[str, str]:
    """健康检查。"""
    return {"status": "ok" if "graph" in _state else "warming"}


# 静态前端：挂载 static/ 目录，``GET /`` 返回 index.html。
_STATIC_DIR = PROJECT_ROOT / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """返回测试用前端页面。"""
    index_path = _STATIC_DIR / "index.html"
    with open(index_path, encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/resume", response_class=HTMLResponse)
async def resume_page() -> HTMLResponse:
    """返回 resume_agent 专用前端页面。"""
    index_path = _STATIC_DIR / "resume.html"
    with open(index_path, encoding="utf-8") as f:
        return HTMLResponse(f.read())
