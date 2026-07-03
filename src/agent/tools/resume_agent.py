"""resume_agent 子智能体：Tool 定义 + 包装节点函数。

与 ``rag_agent`` 的集成方式**完全一致**：

- ``resume_agent``（``@tool`` 装饰）：供 LLM ``bind_tools`` 的 Tool 对象。
- ``resume_agent_node``：异步 LangGraph 节点函数。首次调用时从 ``config`` 获取
  主图 checkpointer（可能为 ``_CustomCheckpointerAdapter`` 等异步适配器），
  惰性编译子图并缓存，此后复用。子图调用使用 ``ainvoke`` 兼容异步 checkpointer。

与 rag_agent 的**唯一差异**：resume 子图内部用 ``interrupt()`` 挂起。子图 ainvoke
会抛 ``GraphInterrupt``，wrapper 必须透传该异常让主图线程也暂停；下一轮主图
``Command(resume=...)`` 会精准恢复到子图挂起点（原型 phase5 已验证）。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.errors import GraphInterrupt
from pydantic import Field

from agent.debug import dlog
from agent.state import MainState
from resume_agent.graph import build_resume_workflow


@tool
def resume_agent(
    intent: str = Field(
        description=(
            "用户修改简历的需求描述。"
            "由你（主图 LLM）从用户消息中精炼概括的优化意图，例如"
            "「补充项目经历」「精简技能列表」「调整章节顺序」。"
            "用户原始简历文本由后端从对话历史自动提取，不要在此参数中塞入整段简历。"
        )
    ),
) -> str:
    """进入简历优化模式，按用户意图对简历草稿进行多轮 CRUD 优化。

    适用场景：用户明确要求"优化简历""改简历""补充项目经历"等修改简历的意图。
    调用后系统会进入简历优化子流程：先制定计划并请你确认，再逐步执行修改，
    每步修改后需你"批准/拒绝/建议"。完成前会一直停留在子流程中。

    不要在普通知识问答场景调用此工具——知识检索请用 ``rag_agent``。
    """
    raise RuntimeError(
        "resume_agent tool 不应被 ToolNode 执行——应由 route_after_chat 拦截"
    )


async def resume_agent_node(
    state: MainState, config: RunnableConfig
) -> dict[str, Any]:
    """异步 resume_agent 包装节点。

    首次调用时从 ``config.configurable.__pregel_checkpointer`` 获取主图 checkpointer
    （可能为异步适配器），惰性编译子图并缓存。子图调用使用 ``ainvoke`` 兼容异步
    checkpointer。

    子图 ``interrupt()`` 时 ``ainvoke`` 抛 ``GraphInterrupt``——本节点**透传**该异常，
    让主图线程暂停在 wrapper 节点；用户下一轮的 ``Command(resume=...)`` 会经主图
    重新进入本节点，子图靠 checkpointer 从挂起点恢复继续（而非重跑）。
    """
    # ── 惰性编译子图（复用主图 checkpointer），只一次 ──
    if getattr(resume_agent_node, "_resume_graph", None) is None:
        cp = config.get("configurable", {}).get("__pregel_checkpointer")
        dlog("resume", "resume_agent_node", "首次惰性编译 RESUME 子图",
             checkpointer=type(cp).__name__ if cp else "None")
        resume_agent_node._resume_graph = build_resume_workflow().compile(  # type: ignore[attr-defined]
            name="ResumeAgent",
            checkpointer=cp,
        )
    rg = resume_agent_node._resume_graph  # type: ignore[attr-defined]

    # ── 提取参数（intent）与原始简历 + 真实 tool_call_id ──
    messages = state.get("messages", [])
    intent = ""
    tool_call_id = "resume_agent"
    if messages:
        last = messages[-1]
        tcs = getattr(last, "tool_calls", []) or []
        for tc in tcs:
            if tc.get("name") == "resume_agent":
                intent = str(tc.get("args", {}).get("intent", ""))
                tool_call_id = str(tc.get("id", tool_call_id))
                break

    # 原始简历：取该 tool_call 之前最近一条实质 HumanMessage 内容
    original_resume = ""
    for m in reversed(messages[:-1] if len(messages) > 1 else []):
        if isinstance(m, HumanMessage):
            content = m.content if isinstance(m.content, str) else str(m.content)
            if content.strip():
                original_resume = content
                break

    dlog("resume", "resume_agent_node", "调用 RESUME 子图",
         intent=intent, resume_len=len(original_resume), tool_call_id=tool_call_id)

    # ── 调用子图（interrupt 时透传 GraphInterrupt） ──
    # rag_agent_node 的 ainvoke 在子图 interrupt 时会抛 GraphInterrupt，
    # 这里**不 catch**——透传给主图 Pregel 循环，让主图线程暂停在 wrapper 节点。
    # 用户下一轮 Command(resume=...) 会经主图重新进入本节点，子图靠 checkpointer
    # 从挂起点恢复继续（而非重跑）。
    try:
        result = await rg.ainvoke(
            {"intent": intent, "original_resume": original_resume},
            config,
        )
    except GraphInterrupt:
        dlog("resume", "resume_agent_node", "子图 interrupt，透传 GraphInterrupt（主图将挂起）")
        raise

    dlog("resume", "resume_agent_node", "RESUME 子图正常完成",
         result_keys=list(result.keys()) if isinstance(result, dict) else type(result).__name__)

    # ── 子图 finalize：转发 ToolMessage + current_draft 到主图 ──
    sub_msgs: list[Any] = result.get("messages", [])
    # finalize 产出的 ToolMessage（tool_call_id="resume_finalize"）需要改写为
    # 主图对应的真实 tool_call_id，否则 chat_node 重入时 OpenAI 会因
    # "assistant tool_calls 未被响应" 报 400。
    out_msgs: list[Any] = []
    for m in sub_msgs:
        if isinstance(m, ToolMessage) and getattr(m, "tool_call_id", "") == "resume_finalize":
            out_msgs.append(
                ToolMessage(content=str(m.content), tool_call_id=tool_call_id)
            )
    if not out_msgs:
        # 兜底：构造一个响应主图 tool_call 的 ToolMessage
        draft = result.get("current_draft", "")
        out_msgs = [ToolMessage(content=f"简历优化完成。最终草稿:\n{draft}", tool_call_id=tool_call_id)]

    return {
        "messages": out_msgs,
        "current_resume": result.get("current_draft", ""),
    }
