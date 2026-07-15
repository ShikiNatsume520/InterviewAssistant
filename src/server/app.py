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

import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, AsyncIterator, Literal, cast
from urllib.parse import urlparse

from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import BaseModel, Field, ValidationError
from sse_starlette.sse import EventSourceResponse

from agents.main.graph import build_main_graph
from agents.rag.state import Citation
from kernel.config import COOKIE_SECURE, DEV_ACCESS_TOKEN, DEV_MODE_ENABLED
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
from kernel.persistence import APP_DB_PATH, CHECKPOINT_DB_PATH, get_store
from kernel.runtime_model import RuntimeModelConfig, use_runtime_model
from server.citations import extract_used_citations
from server.events import (
    EventCursorError,
    EventSource,
    EventType,
    ProductEvent,
    ProductEventStore,
    event_to_dict,
)
from server.identity import (
    AccessDenied,
    ActiveTaskConflict,
    AuthSession,
    IdentityError,
    IdentityThreadStore,
    Principal,
    ThreadRecord,
)

# --------------------------------------------------------------------------- #
# 持久化文件
# --------------------------------------------------------------------------- #

SESSION_COOKIE = "ia_session"
GUEST_BACKUP_COOKIE = "ia_guest_session"


# --------------------------------------------------------------------------- #
# 应用生命周期：持有 AsyncSqliteSaver + 编译后的主图单例
# --------------------------------------------------------------------------- #

_state: dict[str, Any] = {}
"""进程级单例容器：``{"saver": AsyncSqliteSaver, "graph": CompiledStateGraph}``."""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用启动/关闭钩子：初始化 saver + 编译主图。"""
    store = get_store()
    identity = IdentityThreadStore(APP_DB_PATH)
    events = ProductEventStore(APP_DB_PATH)
    async with AsyncSqliteSaver.from_conn_string(str(CHECKPOINT_DB_PATH)) as saver:
        graph = build_main_graph(checkpointer=saver, store=store)
        _state["saver"] = saver
        _state["graph"] = graph
        _state["identity"] = identity
        _state["events"] = events
        try:
            yield
        finally:
            events.close()
            identity.close()
    _state.clear()


app = FastAPI(title="InterviewAssistant", lifespan=lifespan)


@app.exception_handler(HTTPException)
async def http_error_response(request: Request, exc: HTTPException) -> JSONResponse:
    """为 React 客户端提供稳定且不含敏感诊断信息的错误 envelope。"""
    del request
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        error = exc.detail
    else:
        default_codes = {
            400: "BAD_REQUEST",
            401: "SESSION_INVALID",
            404: "RESOURCE_NOT_FOUND",
            409: "TASK_CONFLICT",
            422: "VALIDATION_ERROR",
        }
        error = {
            "code": default_codes.get(exc.status_code, "REQUEST_FAILED"),
            "message": str(exc.detail),
            "retryable": exc.status_code >= 500,
        }
    return JSONResponse(status_code=exc.status_code, content={"error": error})


@app.exception_handler(RequestValidationError)
async def validation_error_response(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """隐藏请求内容，只返回前端可操作的稳定校验错误。"""
    del request, exc
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "请求参数无效",
                "retryable": False,
            }
        },
    )


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


class DeveloperLoginRequest(BaseModel):
    """开发人员登录凭证。"""

    access_token: str


class CreateThreadRequest(BaseModel):
    """创建会话。"""

    title: str = "新会话"


class RenameThreadRequest(BaseModel):
    """重命名会话。"""

    title: str


def _identity_store() -> IdentityThreadStore:
    return cast(IdentityThreadStore, _state["identity"])


def _event_store() -> ProductEventStore:
    return cast(ProductEventStore, _state["events"])


def _set_session_cookie(response: Response, auth: AuthSession) -> None:
    max_age = max(0, int((auth.expires_at - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        SESSION_COOKIE,
        auth.token,
        max_age=max_age,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="strict",
        path="/",
    )


def _set_guest_backup_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        GUEST_BACKUP_COOKIE,
        token,
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="strict",
        path="/",
    )


def _authenticate_token(token: str | None) -> AuthSession:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    try:
        return _identity_store().authenticate(token)
    except IdentityError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="会话无效或已过期"
        ) from exc


def current_auth(
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> AuthSession:
    """解析并滚动续期 HttpOnly 身份 Cookie。"""
    return _authenticate_token(session_token)


def _thread_json(thread: ThreadRecord) -> dict[str, str]:
    return {
        "id": thread.id,
        "title": thread.title,
        "status": thread.status,
        "created_at": thread.created_at,
        "updated_at": thread.updated_at,
    }


def _require_thread(principal: Principal, thread_id: str) -> ThreadRecord:
    try:
        return _identity_store().require_thread(principal, thread_id)
    except AccessDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在"
        ) from exc


def _json_with_session(
    content: Any, auth: AuthSession, status_code: int = 200
) -> JSONResponse:
    response = JSONResponse(content=content, status_code=status_code)
    _set_session_cookie(response, auth)
    return response


def _runtime_model_from_request(
    request: Request, principal: Principal
) -> RuntimeModelConfig | None:
    """开发身份使用 `.env`；游客必须提交本次执行的模型配置请求头。"""
    if principal.kind == "developer":
        return None

    api_key = request.headers.get("X-IA-API-Key", "").strip()
    base_url = request.headers.get("X-IA-Base-URL", "").strip().rstrip("/")
    model = request.headers.get("X-IA-Model", "").strip()
    if not api_key or not base_url or not model:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "MODEL_CONFIG_REQUIRED",
                "message": "请先配置模型服务",
                "retryable": False,
            },
        )
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "MODEL_CONFIG_INVALID",
                "message": "Base URL 必须是有效的 HTTP(S) 地址",
                "retryable": False,
            },
        )
    host = request.url.hostname or ""
    if request.url.scheme != "https" and host not in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "HTTPS_REQUIRED",
                "message": "提交 API Key 必须使用 HTTPS",
                "retryable": False,
            },
        )
    return RuntimeModelConfig(api_key=api_key, base_url=base_url, model=model)


@app.post("/v1/identity/guest")
async def guest_login(
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> JSONResponse:
    """继续当前游客身份；没有有效游客身份时创建一个。"""
    auth: AuthSession | None = None
    if session_token:
        try:
            candidate = _identity_store().authenticate(session_token)
            if candidate.principal.kind == "guest":
                auth = candidate
        except IdentityError:
            pass
    if auth is None:
        auth = _identity_store().create_guest_session()
    return _json_with_session(
        {"principal_id": auth.principal.id, "kind": auth.principal.kind}, auth
    )


@app.get("/v1/capabilities")
async def capabilities() -> dict[str, bool]:
    """返回不含密钥的前端能力开关。"""
    return {"developerLogin": DEV_MODE_ENABLED}


@app.get("/v1/identity")
async def get_identity(auth: AuthSession = Depends(current_auth)) -> JSONResponse:
    """返回当前服务端身份。"""
    return _json_with_session(
        {"principal_id": auth.principal.id, "kind": auth.principal.kind}, auth
    )


@app.post("/v1/identity/developer")
async def developer_login(
    req: DeveloperLoginRequest,
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> JSONResponse:
    """验证仅供本地开发使用的访问凭证并切换身份。"""
    if not DEV_MODE_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="接口不存在")
    guest_token: str | None = None
    if session_token:
        try:
            previous = _identity_store().authenticate(session_token)
            if previous.principal.kind == "guest":
                guest_token = previous.token
        except IdentityError:
            pass
    try:
        auth = _identity_store().create_developer_session(
            req.access_token,
            enabled=DEV_MODE_ENABLED,
            expected_token=DEV_ACCESS_TOKEN,
        )
    except IdentityError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="开发凭证无效"
        ) from exc
    response = _json_with_session(
        {"principal_id": auth.principal.id, "kind": auth.principal.kind}, auth
    )
    if guest_token is not None:
        _set_guest_backup_cookie(response, guest_token)
    return response


@app.post("/v1/identity/guest/reset")
async def restore_guest(
    guest_token: str | None = Cookie(default=None, alias=GUEST_BACKUP_COOKIE),
) -> JSONResponse:
    """退出开发身份，优先恢复此前的游客身份。"""
    auth: AuthSession | None = None
    if guest_token:
        try:
            candidate = _identity_store().authenticate(guest_token)
            if candidate.principal.kind == "guest":
                auth = candidate
        except IdentityError:
            pass
    if auth is None:
        auth = _identity_store().create_guest_session()
    response = _json_with_session(
        {"principal_id": auth.principal.id, "kind": auth.principal.kind}, auth
    )
    response.delete_cookie(GUEST_BACKUP_COOKIE, path="/")
    return response


@app.post("/v1/threads")
async def create_thread(
    req: CreateThreadRequest, auth: AuthSession = Depends(current_auth)
) -> JSONResponse:
    """为当前身份创建会话。"""
    thread = _identity_store().create_thread(auth.principal, req.title)
    return _json_with_session(_thread_json(thread), auth, status.HTTP_201_CREATED)


@app.get("/v1/threads")
async def list_threads(auth: AuthSession = Depends(current_auth)) -> JSONResponse:
    """列出当前身份的会话。"""
    threads = _identity_store().list_threads(auth.principal)
    return _json_with_session([_thread_json(thread) for thread in threads], auth)


@app.get("/v1/threads/{thread_id}")
async def read_thread(
    thread_id: str, auth: AuthSession = Depends(current_auth)
) -> JSONResponse:
    """读取当前身份拥有的会话。"""
    return _json_with_session(
        _thread_json(_require_thread(auth.principal, thread_id)), auth
    )


@app.get("/v1/threads/{thread_id}/events")
async def list_thread_events(
    thread_id: str,
    after_event_id: str | None = Query(default=None, alias="afterEventId"),
    before_event_id: str | None = Query(default=None, alias="beforeEventId"),
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthSession = Depends(current_auth),
) -> JSONResponse:
    """按稳定事件游标读取当前身份拥有的产品时间线。"""
    _require_thread(auth.principal, thread_id)
    try:
        page = _event_store().page(
            thread_id,
            after_event_id=after_event_id,
            before_event_id=before_event_id,
            limit=limit,
        )
    except EventCursorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _json_with_session(
        {
            "events": [event_to_dict(event) for event in page.events],
            "hasMore": page.has_more,
        },
        auth,
    )


@app.patch("/v1/threads/{thread_id}")
async def rename_thread(
    thread_id: str,
    req: RenameThreadRequest,
    auth: AuthSession = Depends(current_auth),
) -> JSONResponse:
    """重命名当前身份拥有的会话。"""
    try:
        thread = _identity_store().rename_thread(auth.principal, thread_id, req.title)
    except AccessDenied as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _json_with_session(_thread_json(thread), auth)


@app.delete("/v1/threads/{thread_id}")
async def delete_thread(
    thread_id: str, auth: AuthSession = Depends(current_auth)
) -> Response:
    """删除会话元数据及其 LangGraph checkpoints。"""
    _require_thread(auth.principal, thread_id)
    try:
        _identity_store().delete_thread(auth.principal, thread_id)
    except ActiveTaskConflict as exc:
        raise HTTPException(status_code=409, detail="会话正在执行") from exc
    await _state["saver"].adelete_thread(thread_id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _set_session_cookie(response, auth)
    return response


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


async def _active_agent_source(graph: Any, config: dict[str, Any]) -> EventSource:
    """仅从 checkpoint 执行状态推断展示头像，不读取产品事件。"""
    state = await graph.aget_state(config)
    names = [str(name) for name in (getattr(state, "next", []) or [])]
    names.extend(
        str(getattr(task, "name", "")) for task in (getattr(state, "tasks", []) or [])
    )
    if any("resume" in name for name in names):
        return "resume"
    if any("research" in name for name in names):
        return "research"
    return "main"


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


def _source_from_namespace(namespace: str) -> EventSource | None:
    if namespace == "main":
        return "main"
    if namespace == "resume_agent":
        return "resume"
    if namespace == "research_agent":
        return "research"
    return None


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(part.get("text", ""))
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    )


def _append_product_event(
    thread_id: str,
    event_type: EventType,
    source: EventSource,
    payload: dict[str, Any],
    *,
    task_id: str | None = None,
) -> ProductEvent | None:
    """追加展示事件；失败只降低可观察性，绝不改变图执行。"""
    try:
        return _event_store().append(
            thread_id, event_type, source, payload, task_id=task_id
        )
    except Exception as exc:
        dlog(
            "server",
            "product_event",
            "事件写入失败，图执行继续",
            event_type=event_type,
            error_type=type(exc).__name__,
        )
        return None


def _event_frame(event: ProductEvent) -> dict[str, str]:
    return {
        "event": "frame",
        "data": json.dumps(
            {"kind": "event", "event": event_to_dict(event)}, ensure_ascii=False
        ),
    }


def _delta_frame(message_id: str, source: EventSource, text: str) -> dict[str, str]:
    return {
        "event": "frame",
        "data": json.dumps(
            {
                "kind": "delta",
                "messageId": message_id,
                "source": source,
                "text": text,
            },
            ensure_ascii=False,
        ),
    }


def _flush_message_buffers(
    buffers: dict[str, tuple[EventSource, str]],
    *,
    thread_id: str,
    task_id: str,
) -> list[dict[str, str]]:
    """只在消息边界持久化完整正文；未到边界的 chunk 仍只在内存。"""
    frames: list[dict[str, str]] = []
    for message_id, (source, text) in buffers.items():
        message_event = _append_product_event(
            thread_id,
            "message.agent",
            source,
            {"messageId": message_id, "text": text},
            task_id=task_id,
        )
        if message_event is not None:
            frames.append(_event_frame(message_event))
    buffers.clear()
    return frames


async def _stream_graph(
    graph: Any,
    input_data: Any,
    config: dict[str, Any],
    *,
    thread_id: str,
    task_id: str,
    initial_source: EventSource,
) -> AsyncIterator[dict[str, str]]:
    """观察图输出并生成只供前端使用的持久事件与临时 delta 帧。"""
    message_buffers: dict[str, tuple[EventSource, str]] = {}
    completed_tools: set[str] = set()
    current_source = initial_source
    last_message_id: str | None = None
    rag_executed = False

    async for ns_tuple, payload in graph.astream(
        input_data,
        config,
        stream_mode="messages",
        subgraphs=True,
    ):
        ns = _normalize_ns(ns_tuple)
        observed_source = _source_from_namespace(ns)
        if observed_source is not None and observed_source != current_source:
            for frame in _flush_message_buffers(
                message_buffers, thread_id=thread_id, task_id=task_id
            ):
                yield frame
            previous_completed = _append_product_event(
                thread_id,
                "task.status",
                current_source,
                {"status": "completed", "label": "已完成"},
                task_id=task_id,
            )
            if previous_completed is not None:
                yield _event_frame(previous_completed)
            transition = _append_product_event(
                thread_id,
                "agent.transition",
                "system",
                {"from": current_source, "to": observed_source},
                task_id=task_id,
            )
            if transition is not None:
                yield _event_frame(transition)
            current_source = observed_source
            status_event = _append_product_event(
                thread_id,
                "task.status",
                current_source,
                {"status": "running", "label": "执行中"},
                task_id=task_id,
            )
            if status_event is not None:
                yield _event_frame(status_event)

        chunk = (
            payload[0] if isinstance(payload, tuple) and len(payload) >= 1 else payload
        )
        metadata = (
            payload[1]
            if isinstance(payload, tuple)
            and len(payload) >= 2
            and isinstance(payload[1], dict)
            else {}
        )
        source = observed_source or current_source
        if metadata.get("langgraph_node") == "rag_agent":
            rag_executed = True
        visible_message = metadata.get("langgraph_node") == "chat_node"
        if isinstance(chunk, AIMessageChunk) and visible_message:
            text = _message_text(chunk.content)
            if text:
                message_id = str(chunk.id or f"{task_id}:{source}")
                last_message_id = message_id
                previous = message_buffers.get(message_id, (source, ""))[1]
                message_buffers[message_id] = (source, previous + text)
                yield _delta_frame(message_id, source, text)
        elif isinstance(chunk, ToolMessage):
            tool_call_id = str(chunk.tool_call_id)
            if tool_call_id not in completed_tools:
                for frame in _flush_message_buffers(
                    message_buffers, thread_id=thread_id, task_id=task_id
                ):
                    yield frame
                completed_tools.add(tool_call_id)
                tool_event = _append_product_event(
                    thread_id,
                    "tool.status",
                    source,
                    {
                        "toolCallId": tool_call_id,
                        "tool": str(chunk.name or "tool"),
                        "status": "completed",
                    },
                    task_id=task_id,
                )
                if tool_event is not None:
                    yield _event_frame(tool_event)

    state = await graph.aget_state(config)
    used_citations: list[Citation] = []
    if rag_executed and last_message_id is not None:
        candidates = list((state.values or {}).get("citations", []) or [])
        buffered = message_buffers.get(last_message_id)
        if buffered is not None and buffered[0] == "main":
            extracted = extract_used_citations(buffered[1], candidates)
            if extracted is not None:
                clean_text, used_citations = extracted
                message_buffers[last_message_id] = (buffered[0], clean_text)

    for frame in _flush_message_buffers(
        message_buffers, thread_id=thread_id, task_id=task_id
    ):
        yield frame

    if used_citations and last_message_id is not None:
        citation_event = _append_product_event(
            thread_id,
            "citation.list",
            current_source,
            {"messageId": last_message_id, "items": used_citations},
            task_id=task_id,
        )
        if citation_event is not None:
            yield _event_frame(citation_event)

    pending2, intrs2 = await _pending_interrupt(graph, config)
    dlog("server", "_stream_graph", f"流结束，pending_interrupt={pending2}")
    if pending2:
        interrupt_event = _append_product_event(
            thread_id,
            "interrupt.requested",
            current_source,
            _interrupt_payload(intrs2),
            task_id=task_id,
        )
        if interrupt_event is not None:
            yield _event_frame(interrupt_event)
        status_event = _append_product_event(
            thread_id,
            "task.status",
            current_source,
            {"status": "waiting", "label": "等待确认"},
            task_id=task_id,
        )
        if status_event is not None:
            yield _event_frame(status_event)
    else:
        completed_event = _append_product_event(
            thread_id,
            "task.completed",
            current_source,
            {"status": "completed", "label": "已完成"},
            task_id=task_id,
        )
        if completed_event is not None:
            yield _event_frame(completed_event)


async def _run_locked_stream(
    source: AsyncIterator[dict[str, str]],
    principal: Principal,
    task_id: str,
    graph: Any,
    config: dict[str, Any],
    thread_id: str,
    source_agent: EventSource,
    runtime_model: RuntimeModelConfig | None,
) -> AsyncIterator[dict[str, str]]:
    """在 SSE 生命周期内持有单任务锁，断连或异常时标记为 interrupted。"""
    final_status: Literal["idle", "interrupted"] = "interrupted"
    try:
        with use_runtime_model(runtime_model):
            async for event in source:
                yield event
        pending, _ = await _pending_interrupt(graph, config)
        final_status = "interrupted" if pending else "idle"
    except asyncio.CancelledError:
        _append_product_event(
            thread_id,
            "task.interrupted",
            source_agent,
            {"status": "interrupted", "label": "已中断"},
            task_id=task_id,
        )
        raise
    except Exception as exc:
        failed = _append_product_event(
            thread_id,
            "task.failed",
            source_agent,
            {
                "status": "failed",
                "label": "执行失败",
                "error": type(exc).__name__,
            },
            task_id=task_id,
        )
        if failed is not None:
            yield _event_frame(failed)
    finally:
        _identity_store().release_task(
            principal,
            task_id,
            status=final_status,
        )


@app.post("/v1/chat")
async def chat(
    req: ChatRequest,
    request: Request,
    auth: AuthSession = Depends(current_auth),
) -> EventSourceResponse:
    """流式对话端点（前端发送按钮 POST）。

    SSE 事件：``token``（``{ns, text}``，ns=main/resume_agent）/ ``interrupt`` /
    ``done``（含 current_resume）。
    """
    _require_thread(auth.principal, req.thread_id)
    runtime_model = _runtime_model_from_request(request, auth.principal)
    graph = _state["graph"]
    config: dict[str, Any] = {
        "configurable": {
            "thread_id": req.thread_id,
            "user_id": auth.principal.id,
        }
    }
    dlog(
        "server",
        "/v1/chat",
        "收到请求",
        user_id=auth.principal.id,
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
            resolved = _append_product_event(
                req.thread_id,
                "interrupt.resolved",
                "user",
                {"phase": phase, "decision": value},
                task_id=task_id,
            )
            if resolved is not None:
                yield _event_frame(resolved)
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
                "user_id": auth.principal.id,
            }
            user_event = _append_product_event(
                req.thread_id,
                "message.user",
                "user",
                {"text": req.message},
                task_id=task_id,
            )
            if user_event is not None:
                yield _event_frame(user_event)
            dlog("server", "/v1/chat", "用新 HumanMessage 启动一轮")

        started = _append_product_event(
            req.thread_id,
            "task.started",
            initial_source,
            {"status": "running", "label": "执行中"},
            task_id=task_id,
        )
        if started is not None:
            yield _event_frame(started)

        async for ev in _stream_graph(
            graph,
            input_data,
            config,
            thread_id=req.thread_id,
            task_id=task_id,
            initial_source=initial_source,
        ):
            yield ev

    initial_source = await _active_agent_source(graph, config)
    task_id = secrets.token_urlsafe(18)
    try:
        _identity_store().acquire_task(auth.principal, req.thread_id, task_id)
    except ActiveTaskConflict as exc:
        raise HTTPException(status_code=409, detail="已有任务正在执行") from exc
    response = EventSourceResponse(
        _run_locked_stream(
            event_gen(),
            auth.principal,
            task_id,
            graph,
            config,
            req.thread_id,
            initial_source,
            runtime_model,
        )
    )
    _set_session_cookie(response, auth)
    return response


@app.post("/v1/checkpoint")
async def checkpoint(
    req: CheckpointRequest,
    request: Request,
    auth: AuthSession = Depends(current_auth),
) -> EventSourceResponse:
    """从最近 checkpoint 续跑端点（前端 init 末尾 POST）。

    ``invoke(None, config)`` 让 LangGraph 自动加载最近 checkpoint 继续执行下一
    超级步。用于刷新恢复 / 页面加载后自动续跑卡在节点间的执行。
    """
    _require_thread(auth.principal, req.thread_id)
    runtime_model = _runtime_model_from_request(request, auth.principal)
    graph = _state["graph"]
    config: dict[str, Any] = {
        "configurable": {
            "thread_id": req.thread_id,
            "user_id": auth.principal.id,
        }
    }
    dlog("server", "/v1/checkpoint", "收到请求", thread_id=req.thread_id)

    async def event_gen() -> AsyncIterator[dict[str, str]]:
        started = _append_product_event(
            req.thread_id,
            "task.started",
            initial_source,
            {"status": "running", "label": "执行中"},
            task_id=task_id,
        )
        if started is not None:
            yield _event_frame(started)
        state = await graph.aget_state(config)
        next_nodes = list(getattr(state, "next", []) or [])
        dlog(
            "server",
            "/v1/checkpoint",
            f"next={next_nodes}, tasks_n={len(getattr(state, 'tasks', []) or [])}",
        )
        if not next_nodes:
            pending, intrs = await _pending_interrupt(graph, config)
            if pending:
                interrupt_event = _append_product_event(
                    req.thread_id,
                    "interrupt.requested",
                    initial_source,
                    _interrupt_payload(intrs),
                    task_id=task_id,
                )
                if interrupt_event is not None:
                    yield _event_frame(interrupt_event)
                status_event = _append_product_event(
                    req.thread_id,
                    "task.status",
                    initial_source,
                    {"status": "waiting", "label": "等待确认"},
                    task_id=task_id,
                )
                if status_event is not None:
                    yield _event_frame(status_event)
            else:
                completed = _append_product_event(
                    req.thread_id,
                    "task.completed",
                    initial_source,
                    {"status": "completed", "label": "已完成"},
                    task_id=task_id,
                )
                if completed is not None:
                    yield _event_frame(completed)
            return
        # invoke(None) 从最近 checkpoint 续跑
        async for ev in _stream_graph(
            graph,
            None,
            config,
            thread_id=req.thread_id,
            task_id=task_id,
            initial_source=initial_source,
        ):
            yield ev

    initial_source = await _active_agent_source(graph, config)
    task_id = secrets.token_urlsafe(18)
    try:
        _identity_store().acquire_task(auth.principal, req.thread_id, task_id)
    except ActiveTaskConflict as exc:
        raise HTTPException(status_code=409, detail="已有任务正在执行") from exc
    response = EventSourceResponse(
        _run_locked_stream(
            event_gen(),
            auth.principal,
            task_id,
            graph,
            config,
            req.thread_id,
            initial_source,
            runtime_model,
        )
    )
    _set_session_cookie(response, auth)
    return response


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
    if first.startswith("research_agent"):
        return "research_agent"
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
async def get_state(
    thread_id: str, auth: AuthSession = Depends(current_auth)
) -> JSONResponse:
    """解析最近 checkpoint，返回前端 init 所需路由 + 渲染数据。

    返回：
    - ``next``：主图待执行节点（空=END）
    - ``pending_interrupt``：当前挂起 interrupt payload（无则 null）
    - ``in_resume``：是否在 resume 子图（next 含 resume_agent 或子图 state 存在）
    - ``main``：主图 messages / current_resume / citations
    - ``resume``：resume 子图 state 摘要（非 resume 会话为 null）
    """
    _require_thread(auth.principal, thread_id)
    graph = _state["graph"]
    config: dict[str, Any] = {
        "configurable": {"thread_id": thread_id, "user_id": auth.principal.id}
    }
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

    body = {
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
    return _json_with_session(body, auth)


@app.get("/v1/thread")
async def get_thread(
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> JSONResponse:
    """返回当前会话 thread_id（主页 init 第一步）。

    兼容旧静态前端：自动恢复当前身份最近的会话；没有时创建持久化会话。
    """
    auth: AuthSession | None = None
    if session_token:
        try:
            auth = _identity_store().authenticate(session_token)
        except IdentityError:
            pass
    if auth is None:
        auth = _identity_store().create_guest_session()
    threads = _identity_store().list_threads(auth.principal)
    thread = (
        threads[0]
        if threads
        else _identity_store().create_thread(auth.principal, "新会话")
    )
    dlog("server", "/v1/thread", "返回最近会话", tid=thread.id)
    return _json_with_session({"thread_id": thread.id}, auth)


@app.get("/health")
async def health() -> dict[str, str]:
    """健康检查。"""
    return {"status": "ok" if "graph" in _state else "warming"}


# 静态前端：挂载 static/ 目录，``GET /`` 返回 index.html。
_STATIC_DIR = PROJECT_ROOT / "static"
_FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
if (_FRONTEND_DIST / "assets").is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=str(_FRONTEND_DIST / "assets")),
        name="frontend-assets",
    )


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """优先返回 React 构建产物；未构建时保留旧测试页便于过渡。"""
    index_path = (
        _FRONTEND_DIST / "index.html"
        if (_FRONTEND_DIST / "index.html").is_file()
        else _STATIC_DIR / "index.html"
    )
    with open(index_path, encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/resume", response_class=HTMLResponse)
async def resume_page() -> HTMLResponse:
    """返回 resume_agent 专用前端页面。"""
    index_path = _STATIC_DIR / "resume.html"
    with open(index_path, encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/{frontend_path:path}", response_class=FileResponse)
async def frontend_route(frontend_path: str) -> FileResponse:
    """为 React Router history 路由回退到构建后的 index.html。"""
    del frontend_path
    index_path = _FRONTEND_DIST / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="前端尚未构建")
    return FileResponse(index_path)
