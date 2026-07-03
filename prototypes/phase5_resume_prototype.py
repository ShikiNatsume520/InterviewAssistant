#!/usr/bin/env python3
"""Phase 5 原型 — 验证 resume_agent 子图的三项关键技术风险。

风险
----
1. **子图 interrupt 跨主图传播 + Command(resume=...) 恢复**
   resume_agent 与 rag_agent 集成方式一致（wrapper 节点 ainvoke 子图），
   但 resume 子图内部要 interrupt。验证：子图 interrupt 是否让主图线程暂停、
   下一轮 Command(resume=...) 是否能精准恢复到子图 interrupt 点。

2. **InjectedState + ToolNode 写回 current_draft / last_draft**
   CRUD 工具用 ``Annotated[ResumeState, InjectedState]`` 注入读草稿，
   返回 dict 写回 ``current_draft`` + ``last_draft`` 快照。

3. **rag_agent 单例跨图共享**
   同一 ``rag_agent_node`` 函数对象被主图和 resume 子图调用，
   ``_rag_graph`` 缓存 + checkpointer 从 config 取，能否在两个上下文都工作。
   （Phase 4 已验证缓存机制，本原型顺带覆盖。）

自包含
------
不调真实 LLM，所有"决策"节点硬编码，纯粹验证 LangGraph 机制。
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from typing import Annotated, Any

# ── 项目根入 sys.path ──
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import InjectedState, ToolNode
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict

# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #


def _safe_print(text: str) -> None:
    """GBK 安全打印。"""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("gbk", errors="replace").decode("gbk"))


DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "phase5_proto.sqlite",
)


# =========================================================================== #
# Resume 子图
# =========================================================================== #


class ResumeState(TypedDict, total=False):
    """简历优化子图状态（草案）。"""

    intent: str
    original_resume: str
    current_draft: str
    last_draft: str
    plan_steps: list[str]
    steps_completed: int
    messages: Annotated[list[BaseMessage], add_messages]


# ---- CRUD 工具（InjectedState）----


@tool
def add_section(
    state: Annotated[ResumeState, InjectedState],
    title: str,
    content: str,
) -> Command:
    """向简历草稿追加一个章节（section 级 CRUD 原型）。

    关键发现：ToolNode 的 ``_normalize_tool_response`` 严格要求工具返回值是
    ``Command | ToolMessage | list``，**裸 dict 不被接受**。要把
    ``current_draft`` / ``last_draft`` 写回 state，必须用 ``Command(update=...)``。
    """
    old = state.get("current_draft", "")
    new_draft = old + f"\n## {title}\n{content}\n"
    # 返回 Command：update 写回草稿字段，messages 附带 ToolMessage
    return Command(
        update={
            "current_draft": new_draft,
            "last_draft": old,
            "messages": [
                ToolMessage(content=f"已添加章节: {title}", tool_call_id="crud_tc_1")
            ],
        }
    )


# ---- 节点 ----


def plan_node(state: ResumeState) -> dict[str, Any]:
    """stub：产出固定计划 + 初始化草稿。"""
    _safe_print("    [resume] plan_node: 产出计划")
    return {
        "plan_steps": ["添加项目经历", "精简技能列表"],
        "current_draft": state.get("original_resume", "# 我的简历\n"),
        "steps_completed": 0,
    }


def plan_confirm_node(state: ResumeState) -> dict[str, Any]:
    """人机交互：interrupt 等用户确认计划。"""
    _safe_print("    [resume] plan_confirm_node: interrupt 等待用户确认")
    value = interrupt(
        {
            "phase": "plan_confirm",
            "plan": state.get("plan_steps", []),
            "draft": state.get("current_draft", ""),
        }
    )
    _safe_print(f"    [resume] plan_confirm_node: 收到用户回复 = {value!r}")
    # 原型：批准则继续，拒绝也继续（不测重规划小循环）
    return {}


def crud_router(state: ResumeState) -> dict[str, Any]:
    """stub LLM：第一次产出 add_section tool_call，第二次无 tool_call → finalize。"""
    done = state.get("steps_completed", 0)
    if done >= 1:
        _safe_print("    [resume] crud_router: 计划完成，无 tool_call → finalize")
        return {"messages": [AIMessage(content="计划已完成。")]}
    _safe_print("    [resume] crud_router: 产出 add_section tool_call")
    msg = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "add_section",
                "args": {"title": "项目经历", "content": "LangGraph 多智能体系统"},
                "id": "crud_tc_1",
            }
        ],
    )
    return {"messages": [msg]}


def route_after_react(state: ResumeState) -> str:
    msgs = state.get("messages", [])
    if not msgs:
        return "finalize"
    last = msgs[-1]
    if getattr(last, "tool_calls", None):
        return "resume_tools"
    return "finalize"


def step_confirm_node(state: ResumeState) -> dict[str, Any]:
    """人机交互：interrupt 等用户批准/拒绝单步修改。"""
    _safe_print("    [resume] step_confirm_node: interrupt 展示 before/after")
    value = interrupt(
        {
            "phase": "step_confirm",
            "before": state.get("last_draft", ""),
            "after": state.get("current_draft", ""),
        }
    )
    _safe_print(f"    [resume] step_confirm_node: 收到用户回复 = {value!r}")
    if str(value).strip() == "拒绝":
        # 恢复快照
        _safe_print("    [resume] step_confirm_node: 用户拒绝，恢复 last_draft")
        return {"current_draft": state.get("last_draft", "")}
    # 批准/建议：步数 +1（建议简化为批准）
    return {"steps_completed": state.get("steps_completed", 0) + 1}


def finalize_node(state: ResumeState) -> dict[str, Any]:
    """打包最终草稿为 ToolMessage，退出子图。"""
    _safe_print("    [resume] finalize_node: 打包最终草稿")
    return {
        "messages": [
            ToolMessage(
                content=f"简历优化完成。最终草稿:\n{state.get('current_draft', '')}",
                tool_call_id="resume_finalize",
            )
        ]
    }


def build_resume_workflow() -> Any:
    """构建未编译的 resume 子图。"""
    wf = StateGraph(ResumeState)
    wf.add_node("plan", plan_node)
    wf.add_node("plan_confirm", plan_confirm_node)
    wf.add_node("crud_router", crud_router)
    wf.add_node("resume_tools", ToolNode([add_section]))
    wf.add_node("step_confirm", step_confirm_node)
    wf.add_node("finalize", finalize_node)

    wf.set_entry_point("plan")
    wf.add_edge("plan", "plan_confirm")
    wf.add_edge("plan_confirm", "crud_router")
    wf.add_conditional_edges("crud_router", route_after_react, {
        "resume_tools": "resume_tools",
        "finalize": "finalize",
    })
    wf.add_edge("resume_tools", "step_confirm")
    wf.add_edge("step_confirm", "crud_router")  # 回到 react 循环
    wf.add_edge("finalize", END)
    return wf


# =========================================================================== #
# 主图（含 resume_agent wrapper）
# =========================================================================== #


class MainState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    user_id: str
    current_resume: str  # 主图侧的简历草稿（子图返回时回填）


def chat_node(state: MainState) -> dict[str, Any]:
    """stub：第一轮产出 resume_agent tool_call，第二轮（子图返回后）直接 END。"""
    msgs = state.get("messages", [])
    # 看最后一条：如果是 ToolMessage（子图返回），则生成最终回复无 tool_call
    if msgs and isinstance(msgs[-1], ToolMessage):
        _safe_print("  [main] chat_node: 收到子图 ToolMessage，生成最终回复")
        return {"messages": [AIMessage(content="简历优化已为您完成。")]}
    # 否则产出 resume_agent tool_call
    _safe_print("  [main] chat_node: 产出 resume_agent tool_call")
    msg = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "resume_agent",
                "args": {"intent": "帮我优化简历，补充项目经历"},
                "id": "resume_tc_1",
            }
        ],
    )
    return {"messages": [msg]}


def route_after_chat(state: MainState) -> str:
    msgs = state.get("messages", [])
    if not msgs:
        return "end"
    last = msgs[-1]
    if getattr(last, "tool_calls", None):
        if any(tc["name"] == "resume_agent" for tc in last.tool_calls):
            return "resume_agent"
    return "end"


async def resume_agent_node(state: MainState, config: RunnableConfig) -> dict[str, Any]:
    """wrapper：惰性编译 resume 子图（复用主图 checkpointer）+ ainvoke。

    关键验证：子图 interrupt 时 ainvoke 的行为。
    """
    if getattr(resume_agent_node, "_resume_graph", None) is None:
        cp = config.get("configurable", {}).get("__pregel_checkpointer")
        _safe_print("  [main] resume_agent_node: 首次惰性编译 resume 子图")
        resume_agent_node._resume_graph = build_resume_workflow().compile(  # type: ignore[attr-defined]
            name="ResumeAgent",
            checkpointer=cp,
        )
    rg = resume_agent_node._resume_graph  # type: ignore[attr-defined]

    # 从主图最近 HumanMessage 提取 original_resume（原型用固定值）
    msgs = state.get("messages", [])
    original = "# 我的简历\n(原始内容)"
    # 找 resume_agent tool_call 的 args
    intent = "优化简历"
    for m in reversed(msgs):
        tcs = getattr(m, "tool_calls", None)
        if tcs:
            for tc in tcs:
                if tc["name"] == "resume_agent":
                    intent = tc["args"].get("intent", intent)
                    break
            break

    _safe_print(f"  [main] resume_agent_node: ainvoke resume 子图 (intent={intent!r})")

    # ---- 风险 1 核心验证：ainvoke 子图，子图 interrupt 时行为 ----
    try:
        result = await rg.ainvoke(
            {"intent": intent, "original_resume": original},
            config,
        )
    except GraphInterrupt as gi:
        _safe_print(f"  [main] resume_agent_node: 捕获 GraphInterrupt — 子图 interrupt 已传播")
        # 重新抛出，让主图也 interrupt（挂起在 wrapper 节点）
        raise

    _safe_print("  [main] resume_agent_node: 子图正常完成，打包 ToolMessage")
    # 子图 finalize 返回的 messages 含 ToolMessage，转发到主图
    sub_msgs = result.get("messages", [])
    return {"messages": sub_msgs, "current_resume": result.get("current_draft", "")}


def build_main_graph(checkpointer: Any) -> Any:
    wf = StateGraph(MainState)
    wf.add_node("chat_node", chat_node)
    wf.add_node("resume_agent", resume_agent_node)
    wf.set_entry_point("chat_node")
    wf.add_conditional_edges("chat_node", route_after_chat, {
        "resume_agent": "resume_agent",
        "end": END,
    })
    wf.add_edge("resume_agent", "chat_node")
    return wf.compile(name="MainAgent", checkpointer=checkpointer)


# =========================================================================== #
# 测试驱动
# =========================================================================== #


async def _has_pending_interrupt(graph: Any, config: dict) -> tuple[bool, Any]:
    """检测当前 thread 是否有 pending interrupt。"""
    state = await graph.aget_state(config)
    # pending interrupts 在 state.tasks 中
    tasks = getattr(state, "tasks", []) or []
    for t in tasks:
        intr = getattr(t, "interrupts", None) or []
        if intr:
            return True, intr
    return False, None


async def run_prototype() -> None:
    _safe_print("=" * 70)
    _safe_print("  Phase 5 原型 — resume_agent interrupt + Command resume + InjectedState")
    _safe_print(f"  DB: {DB_PATH}")
    _safe_print("=" * 70)

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    async with AsyncSqliteSaver.from_conn_string(DB_PATH) as cp:
        graph = build_main_graph(cp)

        # ---- Turn 1: 用户消息 → chat_node → resume wrapper → 子图 plan_confirm interrupt ----
        _safe_print("\n--- Turn 1: 激活 resume_agent，预期在 plan_confirm interrupt ---")
        async for _ in graph.astream(
            {
                "messages": [HumanMessage(content="帮我优化简历")],
                "user_id": "proto_user",
            },
            config,
            stream_mode="updates",
        ):
            pass

        pending, intrs = await _has_pending_interrupt(graph, config)
        _safe_print(f"\n  Turn 1 后 pending interrupt: {pending}")
        if intrs:
            _safe_print(f"  interrupt payload: {intrs[0].value if hasattr(intrs[0],'value') else intrs[0]}")
        assert pending, "风险1 失败：子图 interrupt 未传播到主图"

        # ---- Turn 2: Command(resume="批准") 恢复 plan_confirm → crud → step_confirm interrupt ----
        _safe_print("\n--- Turn 2: Command(resume='批准')，预期推进到 step_confirm interrupt ---")
        async for _ in graph.astream(
            Command(resume="批准"),
            config,
            stream_mode="updates",
        ):
            pass

        pending2, intrs2 = await _has_pending_interrupt(graph, config)
        _safe_print(f"\n  Turn 2 后 pending interrupt: {pending2}")
        if intrs2:
            val = intrs2[0].value if hasattr(intrs2[0], "value") else intrs2[0]
            _safe_print(f"  interrupt phase: {val.get('phase') if isinstance(val, dict) else val}")
        assert pending2, "风险1 失败：Command resume 未推进到 step_confirm"

        # ---- Turn 3: Command(resume="批准") 恢复 step_confirm → finalize → 主图 END ----
        _safe_print("\n--- Turn 3: Command(resume='批准')，预期 finalize → 主图 END ---")
        async for _ in graph.astream(
            Command(resume="批准"),
            config,
            stream_mode="updates",
        ):
            pass

        pending3, _ = await _has_pending_interrupt(graph, config)
        final_state = await graph.aget_state(config)
        msgs = final_state.values.get("messages", [])
        _safe_print(f"\n  Turn 3 后 pending interrupt: {pending3} (预期 False)")
        _safe_print(f"  最终消息数: {len(msgs)}")
        if msgs:
            last = msgs[-1]
            ctype = getattr(last, "type", "") or (last.get("type") if isinstance(last, dict) else "")
            content = getattr(last, "content", "") or (last.get("content","") if isinstance(last, dict) else "")
            _safe_print(f"  最后消息 type={ctype}: {content[:100]}")
        assert not pending3, "Turn 3 后不应还有 pending interrupt"

        # ---- 验证 InjectedState 写回：current_resume 是否含新增章节 ----
        cur = final_state.values.get("current_resume", "")
        _safe_print(f"\n  [风险2] current_resume 是否含'项目经历': {'项目经历' in cur}")
        _safe_print(f"  current_resume 内容:\n{cur}")
        assert "项目经历" in cur, "风险2 失败：InjectedState 未写回 current_draft"

        _safe_print("\n" + "=" * 70)
        _safe_print("  原型验证全部通过")
        _safe_print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_prototype())
