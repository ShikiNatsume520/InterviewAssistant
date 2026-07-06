"""FastAPI 端到端服务（Phase 5）。

暴露 ``/v1/chat`` 端点，接收 ``user_id`` / ``thread_id`` / ``message``，
驱动主图（AsyncSqliteSaver + SqliteStore）以 token 级 SSE 流式输出。

交互模型
--------
- 每个请求一个 ``thread_id``（会话线程），状态持久化在 SQLite。
- 服务端先 ``aget_state`` 查 pending interrupt：
  - 有 → 用 ``Command(resume=message)`` 恢复子图挂起点；
  - 无 → 用新 ``HumanMessage`` 启动一轮。
- ``astream(stream_mode="messages")`` 拿 LLM token chunk，转 SSE event 推送；
  流结束时若仍 pending interrupt，额外发一个 interrupt payload 事件
  （前端据此渲染"批准/拒绝/建议"按钮）。

启动
----
    python -m uvicorn agent.server:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from agent.graph import build_main_graph
from kernel.logging import dlog
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
    """``/v1/chat`` 请求体。"""

    user_id: str = Field(..., description="用户标识，用于长期记忆 Store 键控")
    thread_id: str = Field(..., description="会话线程标识，用于 checkpoint 回放")
    message: str = Field(..., description="本轮用户消息内容")
    resume_value: Any | None = Field(
        default=None,
        description=(
            "恢复 interrupt 时传给子图的值。若提供则优先于 message——"
            "建议场景传 {decision: suggest, suggestion: ...}，"
            "批准/拒绝场景传 approve / reject。"
        ),
    )


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #


async def _pending_interrupt(graph: Any, config: dict[str, Any]) -> tuple[bool, Any]:
    """检测当前 thread 是否有 pending interrupt。"""
    state = await graph.aget_state(config)
    tasks = getattr(state, "tasks", []) or []
    for t in tasks:
        intr = getattr(t, "interrupts", None) or []
        if intr:
            return True, intr
    return False, None


def _interrupt_payload(intrs: Any) -> dict[str, Any]:
    """把 interrupt 对象列表转成可序列化的 dict。"""
    out: list[Any] = []
    for i in intrs:
        val = getattr(i, "value", i)
        out.append(val if isinstance(val, (str, dict, list)) else str(val))
    return {"interrupts": out}


# --------------------------------------------------------------------------- #
# 端点
# --------------------------------------------------------------------------- #


@app.post("/v1/chat")
async def chat(req: ChatRequest) -> EventSourceResponse:
    """流式对话端点。

    SSE 事件类型：
    - ``token``：LLM 输出的 token chunk（``data`` 为文本片段）。
    - ``interrupt``：图挂起等待用户输入（``data`` 为 interrupt payload）。
    - ``done``：本轮结束（``data`` 为最终状态摘要）。
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
        # 先判断是否有 pending interrupt → 决定用 Command resume 还是新消息
        pending, intrs = await _pending_interrupt(graph, config)
        dlog("server", "/v1/chat", f"pending_interrupt={pending}")
        if pending:
            # resume_value 优先；否则用 message（批准/拒绝/简单回复场景）
            value: Any = (
                req.resume_value if req.resume_value is not None else req.message
            )
            input_data: Any = Command(resume=value)
            dlog(
                "server",
                "/v1/chat",
                "用 Command(resume=...) 恢复",
                value_type=type(value).__name__,
            )
            # 先把当前 interrupt payload 推给前端（便于前端知晓上下文）
            yield {
                "event": "interrupt",
                "data": json.dumps(_interrupt_payload(intrs), ensure_ascii=False),
            }
        else:
            input_data = {
                "messages": [HumanMessage(content=req.message)],
                "user_id": req.user_id,
            }
            dlog("server", "/v1/chat", "用新 HumanMessage 启动一轮")

        # token 级流式
        async for chunk, metadata in graph.astream(
            input_data,
            config,
            stream_mode="messages",
        ):
            # chunk 是 AIMessageChunk 或类似；metadata 含 langgraph_node 等
            text = ""
            if isinstance(chunk, str):
                text = chunk
            else:
                content = getattr(chunk, "content", "")
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    # 多模态 content，取 text 部分
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text += str(part.get("text", ""))
            if text:
                yield {"event": "token", "data": text}

        # 流结束后再查一次 interrupt 状态
        pending2, intrs2 = await _pending_interrupt(graph, config)
        dlog("server", "/v1/chat", f"流结束，pending_interrupt={pending2}")
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
            dlog(
                "server",
                "/v1/chat",
                "done",
                last_msg_len=len(last_ai),
                citations_n=len(citations),
            )
            yield {
                "event": "done",
                "data": json.dumps(
                    {"last_message": last_ai[:500], "citations": citations},
                    ensure_ascii=False,
                    default=str,
                ),
            }

    return EventSourceResponse(event_gen())


@app.get("/health")
async def health() -> dict[str, str]:
    """健康检查。"""
    return {"status": "ok" if "graph" in _state else "warming"}


# 静态前端：挂载 static/ 目录，``GET /`` 返回 index.html。
_STATIC_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_STATIC_DIR = os.path.join(_STATIC_DIR, "static")
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """返回测试用前端页面。"""
    index_path = os.path.join(_STATIC_DIR, "index.html")
    with open(index_path, encoding="utf-8") as f:
        return HTMLResponse(f.read())
