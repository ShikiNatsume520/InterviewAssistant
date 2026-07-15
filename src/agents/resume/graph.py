"""resume_agent 子图（后端驱动常驻编辑会话）。

核心范式：**流式 token（namespace 区分）+ 流后 aget_state 查 interrupt**。chat_node
的文本回复由 ``astream(stream_mode="messages", subgraphs=True)`` 实时推前端 token
（主图 ns=main、resume 子图 ns=resume_agent），**不存 state、不进 payload**。interrupt
信号是流终止后用 ``aget_state`` 查 ``task.interrupts[].value`` 得到。intent 由 wrapper
灌入 state，select_resume 的 payload 带出给主页（走 URL 传 /resume）。后端不碰文件 IO：
select_resume 不读文件（用前端回传的 ``{resume_file, resume_shot}``），persist 不写文件
（最终 shot 经 done 回复返前端）。

``chat_node`` 是唯一 LLM 决策点，bind ``request_plan`` / ``grep_replace`` 两个
动作工具，据用户请求自主决策：
- 简单修改 → ``grep_replace``（一波多个）→ edit_executor/approve 逐条小循环
- 复杂/无把握 → ``request_plan`` → plan_node → plan_confirm（人机协同定计划）
- 完成 → 无 tool_call，回复文本即总结 → hitl_standby（常驻待命）

拓扑::

    START → select_resume_node(interrupt: 等前端回传 {resume_file, resume_shot})
          → init_node(入口哨兵)
          → chat_node  ★ 唯一 LLM 决策点（文本走流式 token，不存 state）
              ├─ grep_replace(一波多个) → edit_executor ⟷ approve_node  (逐条小循环)
              │     edit_executor（取一条，只判断不替换）:
              │       ├─ 无待处理 → +"修改完毕" → chat_node
              │       ├─ grep 不到 → +"EditError! ... not in resume_shot" → chat_node
              │       └─ grep 到 → approve_node
              │     approve_node（diff + approve/reject/suggest）:
              │       ├─ approve → 执行replace + 关diff → edit_executor（下一条）
              │       ├─ reject → +"user refuse xxx" → hitl_standby
              │       └─ suggest(+selection) → +"user refuse xxx and his suggestion is xxx" → chat_node
              ├─ request_plan → plan_node → plan_confirm(interrupt)
              │                                ├─ suggest(+selection) → plan_node（注入建议重规划）
              │                                └─ approve → chat_node
              └─ 无 tool_call → hitl_standby(interrupt)
                                  ├─ new_request(+selection) → chat_node
                                  ├─ exit(save=True)  → persist_node(不写文件) → END
                                  └─ exit(save=False) → END

机制要点（phase15 验证：interrupt 透传 + Command(resume) 穿透 + subgraphs=True 子图
token 冒泡 + 主图嵌套子图）：
- chat_node 路由用 conditional_edges + path_map（子图内单决策点，无并行需求）。
- **逐条 approve 小循环**：edit_executor 取一条 grep_replace 判断 grep，命中进 approve_node
  显示 diff；approve 即替换 shot 取下一条（route_after_approve→edit_executor），reject
  转 hitl_standby，suggest 回 chat_node。`processed_edits`（tool_call_id 集合）跟踪已处理，
  逐条推进。chat_node 新发一波是新 AIMessage（新 id），自动覆盖旧波未执行的。
- hitl_standby 路由三态：new_request（注入 HumanMessage）→chat_node；exit(save=True)
  →persist；exit(save=False)→END。靠「是否注入 HumanMessage」+「save」区分，无额外 flag。
- select_resume 仅挂起等前端回传 ``{resume_file, resume_shot}``，**不扫不读文件**；
  persist 仅作 END 前哨，**不写文件**。文件读写全在前端（选单个 .md 文件上传）。
- 各中断 payload 只带业务字段（intent/before/after/edits/plan/summary），不带 chat_node
  文本——文本由 astream 流式 token 推前端。
- 子图无长期记忆：每次进入状态全重置，wrapper 用主图 thread 调用（interrupt 期间状态
  在主图 checkpoint 的子图命名空间持久化）。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from agents.resume.prompts import build_chat_prompt, build_plan_prompt
from agents.resume.state import ResumeState
from agents.resume.tools.edit import EDIT_TOOLS, _apply_grep_replace
from kernel.config import RESUME_MODEL
from kernel.contracts import (
    DecisionInbound,
    GrepReplaceItem,
    HitlInbound,
    PlanConfirmPayload,
    ResumeApprovePayload,
    ResumeHitlPayload,
    ResumeSelectInbound,
    ResumeSelectPayload,
)
from kernel.llm import get_chat_model
from kernel.logging import dlog

# --------------------------------------------------------------------------- #
# 全局 LLM（惰性初始化，与 research 子图同模式）
# --------------------------------------------------------------------------- #

_resume_chat_llm: Any = None
_plan_llm: Any = None


def _init_llms() -> None:
    """惰性初始化 chat_node（bind 编辑/计划工具）与 plan_node 的 LLM。"""
    global _resume_chat_llm, _plan_llm
    if _resume_chat_llm is None:
        _resume_chat_llm = get_chat_model(
            RESUME_MODEL, tools=EDIT_TOOLS + [request_plan]
        )
    if _plan_llm is None:
        _plan_llm = get_chat_model(RESUME_MODEL)


# --------------------------------------------------------------------------- #
# 动作工具：request_plan（仅作路由信号，被路由拦截，不进 ToolNode）
# --------------------------------------------------------------------------- #


@tool
def request_plan() -> str:
    """当修改较复杂、或没把握理解用户意图时调用，进入计划流程让用户协同制定计划。

    本工具不执行任何修改——调用后系统会进入 plan_node 让 LLM 产出修改计划，
    并经 plan_confirm 请用户审阅。调用即表示「当前请求需要先做计划」。
    """
    raise RuntimeError("request_plan tool 不应被执行——应由 route_after_chat 拦截")


# --------------------------------------------------------------------------- #
# select_resume（头部挂起选简历） / persist（尾部哨兵）
# --------------------------------------------------------------------------- #


def select_resume_node(state: ResumeState) -> dict[str, Any]:
    """头部挂起：interrupt 等前端回传 ``{resume_file, resume_shot}``。

    后端驱动范式：本节点**不扫不读文件**——前端用 webkitdirectory 扫本地目录读
    文件后，把 ``{resume_file, resume_shot}`` 回传。payload 最小化，仅带 ``intent``
    （主图精炼的 query，供主页跳转 /resume 走 URL 传前端）。chat_node 文本不进
    payload（流式 token 实时推前端）。

    收到 resume 值后直接取 ``resume_shot`` / ``resume_file`` 灌入 state。
    """
    intent = str(state.get("intent", ""))
    dlog("resume", "select_resume_node", "interrupt 等待前端回传简历", intent=intent)
    value = interrupt(ResumeSelectPayload(intent=intent).model_dump())
    dlog("resume", "select_resume_node", "收到前端回传", value=value)
    # server 已归一化为 {action:"select", resume_file, resume_shot}，schema 校验后取字段
    inbound = ResumeSelectInbound.model_validate(
        value if isinstance(value, dict) else {}
    )
    return {
        "resume_shot": inbound.resume_shot,
        "resume_file": inbound.resume_file,
    }


def persist_node(state: ResumeState) -> dict[str, Any]:
    """尾部哨兵：**不写文件**，仅作 END 前哨。

    仅在 ``hitl_standby`` 收到 ``save=True`` 退出信号时经路由进入本节点。
    ``save=False`` 直接 END。最终 ``resume_shot`` 已在 state，由 wrapper 经主图反馈 +
    后端 ``done`` 回复返前端，**前端负责写回用户本地**（后端不知用户本地路径）。
    """
    shot = state.get("resume_shot", "")
    dlog("resume", "persist_node", "哨兵节点（不写文件）", shot_len=len(shot))
    return {}


# --------------------------------------------------------------------------- #
# init / chat_node / 路由
# --------------------------------------------------------------------------- #


def init_node(state: ResumeState) -> dict[str, Any]:
    """入口哨兵：select_resume → init → chat_node。

    新设计逐条 approve，无整波撤销概念，不再需要 ``last_shot`` 快照。本节点仅记录
    日志，不修改 state。``resume_shot``/``resume_file`` 由 select_resume 灌入，
    首条 HumanMessage 由 wrapper 灌入 messages。
    """
    dlog(
        "resume",
        "init_node",
        "进入会话",
        resume_len=len(state.get("resume_shot", "")),
        resume_file=state.get("resume_file", ""),
        msgs_n=len(state.get("messages", [])),
    )
    return {}


def chat_node(state: ResumeState) -> dict[str, Any]:
    """唯一 LLM 决策点：据 resume_shot + 计划 + 用户请求，决定走 edit / plan / 完成。

    - 调用 ``grep_replace`` → 路由到 edit_executor 串行执行
    - 调用 ``request_plan`` → 路由到 plan_node
    - 无 tool_call → 回复文本即总结，存 ``last_summary``，路由到 hitl_standby

    chat_node 文本**不存 state**——流式范式下由 ``astream(subgraphs=True)`` 实时推
    前端 token（ns=resume_agent），interrupt payload 不带 LLM 文本。选区不再存 state，
    suggest 的 selection 由节点注入 HumanMessage，chat_node 从 messages 读。
    """
    _init_llms()
    resume_shot = state.get("resume_shot", "")
    plan = state.get("plan", [])
    system_prompt = build_chat_prompt(resume_shot, plan, None)
    prompt = ChatPromptTemplate.from_messages(
        [("system", system_prompt), ("placeholder", "{messages}")]
    )
    chain = prompt | _resume_chat_llm
    response = chain.invoke({"messages": state.get("messages", [])})
    tcs = getattr(response, "tool_calls", []) or []
    if tcs:
        dlog(
            "resume",
            "chat_node",
            "LLM 决策调用工具",
            tools=[t.get("name") for t in tcs],
        )
        return {"messages": [response]}
    content = (
        response.content if isinstance(response.content, str) else str(response.content)
    )
    dlog("resume", "chat_node", "LLM 无 tool_call（总结）", summary_len=len(content))
    return {"messages": [response], "last_summary": content}


def route_after_chat(state: ResumeState) -> str:
    """据 ``messages[-1].tool_calls`` 分发：edit / plan / hitl_standby。"""
    msgs = state.get("messages", [])
    if not msgs:
        return "hitl_standby"
    last = msgs[-1]
    tcs = getattr(last, "tool_calls", []) or []
    if not tcs:
        return "hitl_standby"
    name = str(tcs[0].get("name", ""))
    if name == "request_plan":
        return "plan_node"
    if name == "grep_replace":
        return "edit_executor"
    dlog("resume", "route_after_chat", f"未知工具 {name} → hitl_standby")
    return "hitl_standby"


# --------------------------------------------------------------------------- #
# plan 分支
# --------------------------------------------------------------------------- #


def _extract_last_intent(state: ResumeState) -> str:
    """提取最新用户请求文本（最近一条 HumanMessage 内容），供 plan_node 规划。"""
    for m in reversed(state.get("messages", [])):
        if isinstance(m, HumanMessage):
            c = m.content if isinstance(m.content, str) else str(m.content)
            return c
    return ""


def plan_node(state: ResumeState) -> dict[str, Any]:
    """LLM 据简历 + 最新请求产出修改计划（步骤列表），存 ``plan``。

    进入本节点意味着 chat_node 调用了 ``request_plan``——其产出的
    AIMessage(tool_calls=[request_plan]) 必须被 ToolMessage 响应，否则后续
    chat_node 再次 invoke 时 OpenAI 会因「tool_calls 未被响应」报 400。故本节点
    在产出计划的同时，补一条 ToolMessage 响应 ``request_plan`` 的 tool_call_id
    （与主图子 agent wrapper 必须返 ToolMessage 同理）。

    suggest 回到本节点重规划时 ``messages[-1]`` 是 HumanMessage（无 tool_calls），
    不补——避免孤儿 ToolMessage。
    """
    _init_llms()
    resume = state.get("resume_shot", "")
    intent = _extract_last_intent(state)
    dlog("resume", "plan_node", "进入规划", intent=intent, resume_len=len(resume))
    resp = _plan_llm.invoke(build_plan_prompt(resume=resume, intent=intent))
    raw = resp.content if isinstance(resp.content, str) else str(resp.content)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if "```" in raw:
            raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()
    try:
        steps = json.loads(raw)
        if not isinstance(steps, list):
            steps = []
        steps = [str(s) for s in steps if s]
    except json.JSONDecodeError:
        steps = []
    if not steps:
        steps = ["按用户请求直接优化简历内容"]
    dlog("resume", "plan_node", "规划完成", steps_n=len(steps), steps=steps)

    out: dict[str, Any] = {"plan": steps}
    # 补 ToolMessage 响应 chat_node 的 request_plan tool_call（若有）
    msgs = state.get("messages", [])
    if msgs:
        last = msgs[-1]
        tcs = getattr(last, "tool_calls", []) or []
        if tcs:
            tc_id = str(tcs[0].get("id", "request_plan"))
            out["messages"] = [
                ToolMessage(
                    content=f"已进入计划流程，生成计划: {steps}",
                    tool_call_id=tc_id,
                )
            ]
    return out


def plan_confirm_node(state: ResumeState) -> dict[str, Any]:
    """人机交互：interrupt 等用户审阅计划（approve / suggest）。"""
    plan = state.get("plan", [])
    dlog("resume", "plan_confirm_node", "interrupt 等待用户确认计划", plan_n=len(plan))
    value = interrupt(PlanConfirmPayload(plan=plan).model_dump())
    dlog("resume", "plan_confirm_node", "收到用户回复", value=value)
    # server 已归一化为 {action: approve|suggest, suggestion?, selection?}
    inbound = DecisionInbound.model_validate(value if isinstance(value, dict) else {})
    if inbound.action == "suggest":
        msg = _build_suggestion_message(
            "用户对计划的建议", inbound.suggestion, inbound.selection
        )
        return {
            "plan": [],  # 触发回 plan_node 重新规划
            "messages": [msg],
        }
    return {}  # approve


def route_after_plan_confirm(state: ResumeState) -> str:
    """计划确认后：plan 被清空（suggest）回 plan 重规划，否则回 chat_node。"""
    if not state.get("plan"):
        dlog("resume", "route_after_plan_confirm", "→ plan_node (重规划)")
        return "plan_node"
    dlog("resume", "route_after_plan_confirm", "→ chat_node")
    return "chat_node"


# --------------------------------------------------------------------------- #
# edit 分支：逐条 approve 小循环（edit_executor ⟷ approve_node）
# --------------------------------------------------------------------------- #


def _build_suggestion_message(
    prefix: str, suggestion: str, selection: str
) -> HumanMessage:
    """构造 suggest 分支的注入 HumanMessage。

    前端 suggest 可带 selection（用户在 shot 里选中的原文片段）。若有 selection，
    注入「{prefix}（选区: ...）: {suggestion}」，让 chat_node/plan_node 据选区+建议处理；
    无 selection 则「{prefix}: {suggestion}」。
    """
    if selection:
        content = f"{prefix}（选区: {selection}）: {suggestion}"
    else:
        content = f"{prefix}: {suggestion}"
    return HumanMessage(content=content)


def _collect_pending_edits(state: ResumeState) -> list[dict[str, str]]:
    """提取最近一条带 grep_replace tool_calls 的 AIMessage 的全部编辑指令。

    从 messages 倒找最近一条带 grep_replace tool_calls 的 AIMessage，提取全部
    grep_replace 的 ``{id, grep_target, replace_content}``。

    只取最近一条 AIMessage——chat_node 新发一波指令是新 AIMessage（新 tool_call_id），
    自动覆盖旧波未执行的（旧波不再提取）。供 edit_executor_node / approve_node 取
    当前待处理条目。
    """
    from langchain_core.messages import AIMessage

    for m in reversed(state.get("messages", [])):
        if isinstance(m, AIMessage):
            tcs = getattr(m, "tool_calls", []) or []
            if not tcs:
                break
            edits: list[dict[str, str]] = []
            for tc in tcs:
                if str(tc.get("name", "")) != "grep_replace":
                    continue
                args = tc.get("args", {}) or {}
                edits.append(
                    {
                        "id": str(tc.get("id", "")),
                        "grep_target": str(args.get("grep_target", "")),
                        "replace_content": str(args.get("replace_content", "")),
                    }
                )
            return edits
    return []


def _current_edit(state: ResumeState) -> dict[str, str] | None:
    """返回当前待处理的 grep_replace（最近 AIMessage 的第一个未处理 tool_call）。

    ``processed_edits`` 记录已处理（approve/reject/suggest/EditError）的 tool_call_id，
    据此跳过，逐条推进小循环。全部处理完返 None。
    """
    edits = _collect_pending_edits(state)
    processed = state.get("processed_edits", []) or []
    for e in edits:
        if e["id"] not in processed:
            return e
    return None


def _all_pending_ids(state: ResumeState) -> list[str]:
    """返回当前 AIMessage 所有 grep_replace tool_call 的 id（含已处理）。"""
    return [e["id"] for e in _collect_pending_edits(state)]


def _tool_msgs_for_remaining(
    state: ResumeState, current: dict[str, str], current_content: str
) -> list[ToolMessage]:
    """给当前 tc + 剩余未处理 tc 产 ToolMessage（按 AIMessage tool_calls 顺序）。

    不构造 HumanMessage——EditError/拒绝/建议信息直接作为 ToolMessage content 返回，
    剩余未执行 tc 用"前序工具执行被中止，未执行"。全 ToolMessage 紧跟
    ``AIMessage(tool_calls)``，满足 OpenAI 约束（每个 tool_call_id 有 ToolMessage 响应）。
    已 processed 的 tc 跳过（approve 时已产 ToolMessage）。
    """
    edits = _collect_pending_edits(state)
    processed = state.get("processed_edits", []) or []
    msgs: list[ToolMessage] = []
    for e in edits:
        if e["id"] in processed:
            continue
        if e["id"] == current["id"]:
            msgs.append(ToolMessage(content=current_content, tool_call_id=e["id"]))
        else:
            msgs.append(
                ToolMessage(content="前序工具执行被中止，未执行", tool_call_id=e["id"])
            )
    return msgs


def edit_executor_node(state: ResumeState) -> dict[str, Any]:
    """取一条待处理 grep_replace，判断能否 grep 到——不替换，只判断。

    - 无待处理 → 不加消息（所有 tc 已 approve 有 ToolMessage）→ route chat_node
    - grep 不到 → 当前 tc ToolMessage("EditError! ...") + 剩余未处理 tc
      ToolMessage("前序被中止") + 全部 processed → route chat_node（chat_node 据最新 shot 重发覆盖）
    - grep 到 → 不动 shot，route approve_node（approve 时才替换）
    """
    current = _current_edit(state)
    if current is None:
        dlog("resume", "edit_executor_node", "无待处理指令 → chat_node")
        return {}
    shot = state.get("resume_shot", "")
    _new_draft, err = _apply_grep_replace(
        shot, current["grep_target"], current["replace_content"]
    )
    if err:
        preview = current["grep_target"][:50] + (
            "..." if len(current["grep_target"]) > 50 else ""
        )
        dlog("resume", "edit_executor_node", "EditError，回 chat_node", preview=preview)
        # 当前 tc ToolMessage(EditError) + 剩余 tc ToolMessage(前序被中止)，全部标记 processed
        tool_msgs = _tool_msgs_for_remaining(
            state, current, f"EditError! {preview} not in resume_shot"
        )
        return {
            "messages": tool_msgs,
            "processed_edits": _all_pending_ids(state),
        }
    dlog(
        "resume",
        "edit_executor_node",
        "grep 命中 → approve_node",
        target_preview=current["grep_target"][:30],
    )
    return {}  # route approve_node


def route_after_edit(state: ResumeState) -> str:
    """edit_executor 后路由：无待处理/ grep 不到 → chat_node；grep 到 → approve_node。"""
    current = _current_edit(state)
    if current is None:
        return "chat_node"  # 修改完毕
    shot = state.get("resume_shot", "")
    _new_draft, err = _apply_grep_replace(
        shot, current["grep_target"], current["replace_content"]
    )
    if err:
        return "chat_node"  # EditError
    return "approve_node"


def approve_node(state: ResumeState) -> dict[str, Any]:
    """Interrupt 展示当前条 diff（before/after/edits 单条），三选一。

    不构造 HumanMessage——决策信息直接作 ToolMessage content，剩余未执行 tc 用
    "前序被中止"。全 ToolMessage 紧跟 ``AIMessage(tool_calls)`` 满足 OpenAI 约束。

    - approve → 执行 replace（resume_shot=after）+ 当前 tc ToolMessage("已替换")
      + processed 追加 + approve_decision → route edit_executor（取下一条）
    - reject → 当前 tc ToolMessage("用户拒绝") + 剩余 tc ToolMessage("前序被中止")
      + 全部 processed + approve_decision → route hitl_standby
    - suggest → 当前 tc ToolMessage("用户拒绝并建议: ...") + 剩余 tc ToolMessage("前序被中止")
      + 全部 processed + approve_decision → route chat_node
    """
    current = _current_edit(state)
    # current 必非空（edit_executor 已校验 grep 到）；防御性处理
    if current is None:
        return {"approve_decision": "approve"}
    before = state.get("resume_shot", "")
    after, _err = _apply_grep_replace(
        before, current["grep_target"], current["replace_content"]
    )
    edits = [
        GrepReplaceItem(
            grep_target=current["grep_target"],
            replace_content=current["replace_content"],
        )
    ]
    dlog(
        "resume",
        "approve_node",
        "interrupt 等待用户确认",
        before_len=len(before),
        after_len=len(after),
    )
    value = interrupt(
        ResumeApprovePayload(before=before, after=after, edits=edits).model_dump()
    )
    dlog("resume", "approve_node", "收到用户回复", value=value)
    preview = current["grep_target"][:50] + (
        "..." if len(current["grep_target"]) > 50 else ""
    )
    # server 已归一化为 {action: approve|reject|suggest, suggestion?, selection?}
    inbound = DecisionInbound.model_validate(value if isinstance(value, dict) else {})
    if inbound.action == "reject":
        dlog("resume", "approve_node", "用户拒绝 → hitl_standby")
        tool_msgs = _tool_msgs_for_remaining(state, current, f"用户拒绝 {preview}")
        return {
            "messages": tool_msgs,
            "processed_edits": _all_pending_ids(state),
            "approve_decision": "reject",
        }
    if inbound.action == "suggest":
        dlog("resume", "approve_node", "用户建议 → chat_node")
        tool_msgs = _tool_msgs_for_remaining(
            state, current, f"用户拒绝 {preview} 并建议: {inbound.suggestion}"
        )
        return {
            "messages": tool_msgs,
            "processed_edits": _all_pending_ids(state),
            "approve_decision": "suggest",
        }
    dlog("resume", "approve_node", "用户批准 → edit_executor（下一条）")
    return {
        "messages": [
            ToolMessage(content=f"已替换「{preview}」", tool_call_id=current["id"])
        ],
        "resume_shot": after,
        "processed_edits": list(state.get("processed_edits", []) or [])
        + [current["id"]],
        "approve_decision": "approve",
    }


def route_after_approve(state: ResumeState) -> str:
    """approve_node 后路由：approve→edit_executor；reject→hitl_standby；suggest→chat_node。"""
    decision = str(state.get("approve_decision", "approve"))
    if decision == "reject":
        return "hitl_standby"
    if decision == "suggest":
        return "chat_node"
    return "edit_executor"


# --------------------------------------------------------------------------- #
# hitl 待命分支
# --------------------------------------------------------------------------- #


def hitl_standby_node(state: ResumeState) -> dict[str, Any]:
    """常驻待命：interrupt 挂起，等 new_request（注入 HumanMessage）或 exit（设 save）。"""
    dlog("resume", "hitl_standby_node", "interrupt 等待用户信号（新请求/结束）")
    value = interrupt(
        ResumeHitlPayload(summary=state.get("last_summary", "")).model_dump()
    )
    dlog("resume", "hitl_standby_node", "收到用户信号", value=value)
    # server 已归一化为 {action: new_request|exit, request?, selection?, save?}
    inbound = HitlInbound.model_validate(value if isinstance(value, dict) else {})
    if inbound.action == "exit":
        return {"save": inbound.save}
    # new_request：注入用户新请求作 HumanMessage（可带选区 selection 作位置提示）
    request = inbound.request
    selection = inbound.selection
    if selection:
        return {"messages": [HumanMessage(content=f"{request}（选区: {selection}）")]}
    return {"messages": [HumanMessage(content=request)]}


def route_after_hitl(state: ResumeState) -> str:
    """据信号区分：new_request→chat_node / exit(save=True)→persist / exit(save=False)→END。"""
    msgs = state.get("messages", [])
    if msgs and isinstance(msgs[-1], HumanMessage):
        dlog("resume", "route_after_hitl", "→ chat_node (新请求)")
        return "chat_node"
    if state.get("save"):
        dlog("resume", "route_after_hitl", "→ persist (保存退出)")
        return "persist"
    dlog("resume", "route_after_hitl", "→ END (放弃退出)")
    return END


# --------------------------------------------------------------------------- #
# 子图构建
# --------------------------------------------------------------------------- #


def build_resume_workflow() -> Any:
    """构建未编译的 resume 子图。

    返回未编译的 ``StateGraph``，由 ``resume_agent_node``（主图 wrapper）惰性
    调用；模块级 ``graph`` 实例编译时不带 checkpointer → 运行时自动继承父图
    checkpointer（与 rag_agent 一致）。
    """
    workflow = StateGraph(ResumeState)
    workflow.add_node("select_resume", select_resume_node)
    workflow.add_node("init", init_node)
    workflow.add_node("chat_node", chat_node)
    workflow.add_node("plan_node", plan_node)
    workflow.add_node("plan_confirm", plan_confirm_node)
    workflow.add_node("edit_executor", edit_executor_node)
    workflow.add_node("approve_node", approve_node)
    workflow.add_node("hitl_standby", hitl_standby_node)
    workflow.add_node("persist", persist_node)

    workflow.set_entry_point("select_resume")
    workflow.add_edge("select_resume", "init")
    workflow.add_edge("init", "chat_node")
    workflow.add_conditional_edges(
        "chat_node",
        route_after_chat,
        {
            "plan_node": "plan_node",
            "edit_executor": "edit_executor",
            "hitl_standby": "hitl_standby",
        },
    )
    workflow.add_edge("plan_node", "plan_confirm")
    workflow.add_conditional_edges(
        "plan_confirm",
        route_after_plan_confirm,
        {"plan_node": "plan_node", "chat_node": "chat_node"},
    )
    # edit_executor ⟷ approve_node 逐条 approve 小循环
    workflow.add_conditional_edges(
        "edit_executor",
        route_after_edit,
        {"chat_node": "chat_node", "approve_node": "approve_node"},
    )
    workflow.add_conditional_edges(
        "approve_node",
        route_after_approve,
        {
            "edit_executor": "edit_executor",
            "hitl_standby": "hitl_standby",
            "chat_node": "chat_node",
        },
    )
    workflow.add_conditional_edges(
        "hitl_standby",
        route_after_hitl,
        {"chat_node": "chat_node", "persist": "persist", END: END},
    )
    workflow.add_edge("persist", END)
    return workflow


# 供 langgraph.json / SDK 直接发现的模块级实例（无 checkpointer，纯调试用）
graph = build_resume_workflow().compile(name="resume_agent")
"""供 ``langgraph.json`` 及 ``__init__.py`` 导出的模块级图实例。"""
