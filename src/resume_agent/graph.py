"""resume_agent 子图（计划-确认-执行循环）。

拓扑::

    START → plan_node → plan_confirm_node ⇄ plan_node   (计划小循环)
                              │ 用户批准
                              ▼
                         react_router  ←────────────────────────┐
                         (LLM, bind_tools: crud×5 + rag_agent)  │
                              │                                 │
                     tool=rag_agent  tool=crud  无 tool_call    │
                              │        │          │             │
                              ▼        ▼          ▼             │
                      rag_agent_node  resume_   finalize_node → END
                      (复用单例)      tools     (打包最终草稿)
                              │        │
                              │        ▼
                              │  step_confirm_node (interrupt)
                              │   │批准 → steps_completed+1────┘
                              │   │拒绝 → 恢复 last_draft──────┘
                              │   │建议 → 恢复+注入建议────────┘
                              └───→ react_router

关键机制
--------
- ``plan_confirm_node`` / ``step_confirm_node`` 用 ``interrupt()`` 挂起子图；
  wrapper 节点（``resume_agent_node``）透传 ``GraphInterrupt``，让主图线程暂停，
  下一轮 ``Command(resume=...)`` 精准恢复到子图挂起点（原型验证通过）。
- plan 遵循是**软约束**：``plan_steps`` + ``steps_completed`` 注入 ``react_router``
  的 system prompt，LLM 自主按序执行。
- ``step_confirm`` 拒绝时用 ``last_draft`` 快照恢复（CRUD 工具写入的快照）。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from agent.debug import dlog, slog
from resume_agent.state import ResumeState
from resume_agent.tools.crud import RESUME_CRUD_TOOLS
from src.client import get_chat_model

# --------------------------------------------------------------------------- #
# 全局 LLM（惰性初始化，与 graph.py 的 _chat_model 同模式）
# --------------------------------------------------------------------------- #

_resume_llm: Any = None
"""react_router 使用的 LLM（bind_tools 后），惰性初始化。"""


def _init_llm() -> None:
    """惰性初始化 ``react_router`` 的 LLM 并绑定工具。"""
    global _resume_llm
    if _resume_llm is None:
        # 复用主图的 rag_agent 作为本子图的检索工具（进程内单例缓存）
        from agent.tools.rag_agent import rag_agent

        tools = RESUME_CRUD_TOOLS + [rag_agent]
        _resume_llm = get_chat_model("deepseek-v4-flash", tools=tools)


# --------------------------------------------------------------------------- #
# plan 阶段
# --------------------------------------------------------------------------- #

PLAN_PROMPT = """你是简历优化规划师。根据用户简历与修改意图，制定**可执行的步骤清单**。

规则：
1. 每个步骤是一句具体的操作描述，对应一个 CRUD 工具能完成的动作
   （add_section / update_section / delete_section / reorder_sections）。
2. 步骤数量 2-5 个，按逻辑顺序排列。
3. 如需参考简历模板/写法，可在某步骤中说明"检索模板"（会调用 rag_agent）。
4. 只输出 JSON 数组，不要 markdown 代码块标记，不要解释。

=== 用户简历 ===
{resume}

=== 修改意图 ===
{intent}

输出格式示例：
["添加项目经历章节，突出 LangGraph 多智能体系统", "精简技能列表至 5 项", "重排章节为 教育→项目→技能"]
"""


def plan_node(state: ResumeState) -> dict[str, Any]:
    """LLM 产出 plan_steps + 初始化 current_draft。"""
    _init_llm()
    intent = state.get("intent", "")
    resume = state.get("original_resume", state.get("current_draft", ""))
    dlog("resume", "plan_node", "进入规划", intent=intent, resume_len=len(resume))
    slog("resume", "plan_node", "进入规划", intent=intent, resume_len=len(resume))

    llm = get_chat_model("deepseek-v4-flash")
    prompt = PLAN_PROMPT.format(resume=resume, intent=intent)
    resp = llm.invoke(prompt)
    raw = resp.content if isinstance(resp.content, str) else str(resp.content)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if "```" in raw:
            raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()
    slog("resume", "plan_node", "LLM 返回规划", raw_preview=raw[:200])

    try:
        steps = json.loads(raw)
        if not isinstance(steps, list):
            steps = []
        steps = [str(s) for s in steps if s]
    except json.JSONDecodeError:
        steps = []

    if not steps:
        steps = ["按修改意图直接优化简历内容"]

    dlog("resume", "plan_node", "规划完成", steps_n=len(steps), steps=steps)
    slog("resume", "plan_node", "规划完成", steps_n=len(steps), steps=steps)
    return {
        "plan_steps": steps,
        "steps_completed": 0,
        "current_draft": resume,
    }


def plan_confirm_node(state: ResumeState) -> dict[str, Any]:
    """人机交互：interrupt 等用户确认/建议计划。"""
    plan = state.get("plan_steps", [])
    dlog("resume", "plan_confirm_node", "interrupt 等待用户确认计划", plan_n=len(plan))
    slog("resume", "plan_confirm_node", "interrupt 等待用户确认计划", plan_n=len(plan))
    value = interrupt(
        {
            "phase": "plan_confirm",
            "plan": plan,
            "draft": state.get("current_draft", ""),
        }
    )
    dlog("resume", "plan_confirm_node", "收到用户回复", value=value)
    slog("resume", "plan_confirm_node", "收到用户回复", value=value)
    # value: "approve" | {"decision":"suggest","suggestion":"..."}
    if isinstance(value, dict) and value.get("decision") == "suggest":
        suggestion = str(value.get("suggestion", ""))
        return {
            "plan_steps": [],  # 触发回 plan_node 重新规划
            "messages": [HumanMessage(content=f"用户对计划的建议: {suggestion}")],
        }
    # approve 或裸字符串批准
    return {}


def route_after_plan_confirm(state: ResumeState) -> str:
    """计划确认后：plan_steps 被清空（建议场景）回 plan 重规划，否则进 react。"""
    if not state.get("plan_steps"):
        dlog("resume", "route_after_plan_confirm", "→ plan (重规划)")
        return "plan"
    dlog("resume", "route_after_plan_confirm", "→ react_router")
    return "react_router"


# --------------------------------------------------------------------------- #
# ReAct 执行阶段
# --------------------------------------------------------------------------- #

REACT_PROMPT = """你是简历优化执行器。按计划逐步执行 CRUD 工具修改草稿。

## 当前草稿
{draft}

## 优化计划（已按顺序列出，[done] 为已完成）
{plan_with_progress}

## 工具说明
- add_section(title, content): 追加章节
- update_section(title, new_content): 替换章节正文
- delete_section(title): 删除章节
- reorder_sections(new_order): 重排章节（new_order 须含全部现有章节标题）
- show_draft(): 查看当前草稿
- rag_agent(query, search_type): 检索本地知识库（如需参考简历模板写法）

## 准则
1. 每次只调用一个工具，完成一个计划步骤。
2. 优先按计划顺序执行未完成步骤；若发现某步骤不适用可跳过。
3. 全部步骤完成后，直接回复"优化完成"（不调用工具），进入收尾。
"""


def _render_plan_with_progress(state: ResumeState) -> str:
    steps = state.get("plan_steps", [])
    done = state.get("steps_completed", 0)
    lines = []
    for i, s in enumerate(steps):
        mark = "[done]" if i < done else "[    ]"
        lines.append(f"{i + 1}. {mark} {s}")
    return "\n".join(lines) if lines else "(无计划)"


def react_router(state: ResumeState) -> dict[str, Any]:
    """LLM 决策节点：选工具执行或回复完成。"""
    _init_llm()
    done = state.get("steps_completed", 0)
    total = len(state.get("plan_steps", []))
    dlog(
        "resume",
        "react_router",
        "进入 ReAct 决策",
        steps_completed=done,
        steps_total=total,
    )
    slog(
        "resume",
        "react_router",
        "进入 ReAct 决策",
        steps_completed=done,
        steps_total=total,
    )
    system_prompt = REACT_PROMPT.format(
        draft=state.get("current_draft", ""),
        plan_with_progress=_render_plan_with_progress(state),
    )
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("placeholder", "{messages}"),
        ]
    )
    chain = prompt | _resume_llm
    response = chain.invoke({"messages": state.get("messages", [])})
    tcs = getattr(response, "tool_calls", []) or []
    if tcs:
        dlog(
            "resume",
            "react_router",
            "LLM 决策调用工具",
            tools=[t.get("name") for t in tcs],
        )
        slog(
            "resume",
            "react_router",
            "LLM 决策调用工具",
            tools=[t.get("name") for t in tcs],
        )
    else:
        c = (
            response.content
            if isinstance(response.content, str)
            else str(response.content)
        )
        dlog(
            "resume",
            "react_router",
            "LLM 无 tool_call（将进 finalize）",
            reply_len=len(c),
        )
        slog(
            "resume",
            "react_router",
            "LLM 无 tool_call，将进 finalize",
            reply_len=len(c),
        )
    return {"messages": [response]}


def route_after_react(state: ResumeState) -> str:
    """react_router 之后：有 tool_call → 执行；否则 → finalize。"""
    messages = state.get("messages", [])
    if not messages:
        dlog("resume", "route_after_react", "无消息 → finalize")
        return "finalize"
    last: Any = messages[-1]
    tcs: Any = getattr(last, "tool_calls", None)
    if tcs:
        first = tcs[0]
        name = first.get("name", "")
        target = "rag_agent" if name == "rag_agent" else "resume_tools"
        dlog("resume", "route_after_react", f"→ {target}", tool=name)
        if name == "rag_agent":
            return "rag_agent"
        return "resume_tools"
    dlog("resume", "route_after_react", "→ finalize")
    return "finalize"


async def rag_agent_in_resume_node(
    state: ResumeState, config: RunnableConfig
) -> dict[str, Any]:
    """在 resume 子图内复用主图的 rag_agent_node（单例 _rag_graph 缓存）。"""
    dlog("resume", "rag_agent_in_resume", "复用 rag_agent_node 检索模板/知识")
    slog("resume", "rag_agent_in_resume", "复用 rag_agent_node 检索模板/知识")
    from agent.tools.rag_agent import rag_agent_node

    # 把 ResumeState 的 messages 适配给 rag_agent_node（它读 MainState.messages）
    messages = state.get("messages", [])
    result = await rag_agent_node({"messages": messages}, config)
    dlog(
        "resume",
        "rag_agent_in_resume",
        "rag_agent_node 返回",
        result_keys=list(result.keys()) if isinstance(result, dict) else "?",
    )
    slog(
        "resume",
        "rag_agent_in_resume",
        "rag_agent_node 返回",
        result_keys=list(result.keys()) if isinstance(result, dict) else "?",
    )
    return dict(result)


def step_confirm_node(state: ResumeState) -> dict[str, Any]:
    """人机交互：interrupt 展示 before/after，用户批准/拒绝/建议。"""
    before = state.get("last_draft", "")
    after = state.get("current_draft", "")
    dlog(
        "resume",
        "step_confirm_node",
        "interrupt 等待用户确认单步修改",
        before_len=len(before),
        after_len=len(after),
    )
    slog(
        "resume",
        "step_confirm_node",
        "interrupt 等待用户确认单步修改",
        before_len=len(before),
        after_len=len(after),
    )
    value = interrupt(
        {
            "phase": "step_confirm",
            "before": before,
            "after": after,
        }
    )
    dlog("resume", "step_confirm_node", "收到用户回复", value=value)
    slog("resume", "step_confirm_node", "收到用户回复", value=value)
    # value: "approve" | "reject" | {"decision":"suggest","suggestion":"..."}
    if isinstance(value, dict) and value.get("decision") == "suggest":
        suggestion = str(value.get("suggestion", ""))
        return {
            "current_draft": state.get("last_draft", ""),
            "messages": [HumanMessage(content=f"用户对该步骤的建议: {suggestion}")],
        }
    if str(value).strip() in ("reject", "拒绝"):
        dlog("resume", "step_confirm_node", "用户拒绝，恢复 last_draft")
        slog("resume", "step_confirm_node", "用户拒绝，恢复 last_draft")
        return {"current_draft": state.get("last_draft", "")}
    # approve
    new_done = state.get("steps_completed", 0) + 1
    dlog("resume", "step_confirm_node", "用户批准", steps_completed=new_done)
    slog("resume", "step_confirm_node", "用户批准", steps_completed=new_done)
    return {"steps_completed": new_done}


def finalize_node(state: ResumeState) -> dict[str, Any]:
    """打包最终草稿为 ToolMessage，退出子图。"""
    from langchain_core.messages import ToolMessage

    draft = state.get("current_draft", "")
    done = state.get("steps_completed", 0)
    total = len(state.get("plan_steps", []))
    dlog(
        "resume",
        "finalize_node",
        "打包最终草稿退出子图",
        steps_completed=done,
        steps_total=total,
        draft_len=len(draft),
    )
    slog(
        "resume",
        "finalize_node",
        "打包最终草稿退出子图",
        steps_completed=done,
        steps_total=total,
        draft_len=len(draft),
    )
    summary = f"简历优化完成（完成 {done}/{total} 步）。最终草稿:\n{draft}"
    return {
        "messages": [ToolMessage(content=summary, tool_call_id="resume_finalize")],
        "current_draft": draft,
    }


# --------------------------------------------------------------------------- #
# 子图构建
# --------------------------------------------------------------------------- #


def build_resume_workflow() -> Any:
    """构建未编译的 resume 子图。

    返回未编译的 ``StateGraph``，由 ``resume_agent_node``（主图 wrapper）惰性
    编译并绑定主图 checkpointer。
    """
    workflow = StateGraph(ResumeState)
    workflow.add_node("plan", plan_node)
    workflow.add_node("plan_confirm", plan_confirm_node)
    workflow.add_node("react_router", react_router)
    workflow.add_node("resume_tools", ToolNode(RESUME_CRUD_TOOLS))
    workflow.add_node("rag_agent", rag_agent_in_resume_node)
    workflow.add_node("step_confirm", step_confirm_node)
    workflow.add_node("finalize", finalize_node)

    workflow.set_entry_point("plan")
    workflow.add_edge("plan", "plan_confirm")
    workflow.add_conditional_edges(
        "plan_confirm",
        route_after_plan_confirm,
        {"plan": "plan", "react_router": "react_router"},
    )
    workflow.add_conditional_edges(
        "react_router",
        route_after_react,
        {
            "resume_tools": "resume_tools",
            "rag_agent": "rag_agent",
            "finalize": "finalize",
        },
    )
    workflow.add_edge("resume_tools", "step_confirm")
    workflow.add_edge("rag_agent", "step_confirm")
    workflow.add_edge("step_confirm", "react_router")
    workflow.add_edge("finalize", END)
    return workflow


# 供 langgraph.json / SDK 直接发现的模块级实例（无 checkpointer，纯调试用）
graph = build_resume_workflow().compile(name="resume_agent")
"""供 ``langgraph.json`` 及 ``__init__.py`` 导出的模块级图实例。"""
