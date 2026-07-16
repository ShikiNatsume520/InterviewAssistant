"""resume_agent 子智能体：Tool 定义 + 包装节点函数。

阶段 4B：wrapper 负责按可信身份读取只读源简历，并把工作草稿交给子图；
子图保存退出时创建新的派生简历，放弃退出不写资源库。

职责：
- 从 tool_call 读取最小交接 ``resume_id + user_request?``。
- 从 RunnableConfig 取得 user/thread 身份，校验归属并读取源正文。
- 用稳定 resume_session_id 初始化子图，使保存 checkpoint 重跑保持幂等。
- 子图完成后只返回结构化结果，不把完整草稿塞回 Main Agent上下文。

与 ``rag_agent`` 的集成方式一致：

- ``resume_agent``（``@tool`` 装饰）：供 LLM ``bind_tools`` 的 Tool 对象。
- ``resume_agent_node``：异步 LangGraph 节点函数。直接 ``import`` 模块级预编译的
  resume 子图实例（``resume_agent.graph.graph``，编译时不带 checkpointer → 运行时
  自动继承父图 checkpointer），用 ``ainvoke`` 调用。

子图作为模块级实例被 wrapper 函数体直接引用，``find_subgraph_pregel`` 的 AST
闭包分析可发现它 → ``PregelNode.subgraphs`` 被填充 → Studio 可展开子图内部节点。

resume 子图内部用 ``interrupt()`` 挂起（plan_confirm / approve /
hitl_standby）。子图 ainvoke 会抛 ``GraphInterrupt``，wrapper 必须透传该异常让主图
线程也暂停；下一轮主图 ``Command(resume=...)`` 会精准恢复到子图挂起点（checkpointer
由父图继承，子图状态持久化在共享命名空间）。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.errors import GraphInterrupt
from pydantic import Field

from agents.resume.graph import graph as resume_graph
from kernel.logging import dlog
from kernel.resumes import ResumeRepository


@tool
def resume_agent(
    resume_id: str = Field(
        description=(
            "要修改的源简历 ID，必须来自 list_resumes、search_resumes、read_resume "
            "或系统提供的已验证 ID，禁止编造。源简历只读。"
        )
    ),
    user_request: str | None = Field(
        default=None,
        description=(
            "用户原始修改诉求或忠实的简短概括，可为空。不要为了填充该字段过度追问，"
            "不要生成详细计划或塞入整份简历。"
        ),
    ),
) -> str:
    """进入简历优化模式，按用户意图对简历进行多轮修改并请你确认。

    适用场景：用户明确要求"优化简历""改简历""补充项目经历"等修改简历的意图。
    调用后系统读取指定源简历并进入常驻编辑会话；信息不足时由 Resume Agent结合
    简历内容继续询问。保存退出创建新的派生简历，放弃退出不创建资源。

    不要在普通知识问答场景调用此工具——知识检索请用 ``rag_agent``。
    """
    raise RuntimeError(
        "resume_agent tool 不应被 ToolNode 执行——应由 route_after_chat 拦截"
    )


async def resume_agent_node(
    state: dict[str, Any], config: RunnableConfig
) -> dict[str, Any]:
    """异步 resume_agent 包装节点。

    由 ``route_after_chat`` 经 ``Send("resume_agent", {"tool_call": tc})`` 调用，
    从 Send arg 读取本节点负责的单个 tool_call。

    wrapper 校验并读取源简历，初始化工作草稿；子图退出后返回最小结构化结果。
    """
    dlog("resume", "resume_agent_node", "进入节点")

    tc: dict[str, Any] = state.get("tool_call", {}) or {}
    args = tc.get("args", {}) or {}
    resume_id = str(args.get("resume_id", "")).strip()
    raw_request = args.get("user_request")
    user_request = str(raw_request).strip() if raw_request is not None else None
    if user_request == "":
        user_request = None
    tool_call_id = str(tc.get("id", "resume_agent"))
    configurable = config.get("configurable", {})
    principal_id = str(configurable.get("user_id", ""))
    thread_id = str(configurable.get("thread_id", ""))
    if not principal_id or not resume_id:
        raise RuntimeError("resume_agent missing trusted user_id or resume_id")

    repository = ResumeRepository()
    try:
        source = repository.require(principal_id, resume_id)
    finally:
        repository.close()

    # 可选诉求作为 Resume Agent 的首条用户消息；为空时由其读取简历后主动询问。
    initial_messages: list[Any] = (
        [HumanMessage(content=user_request)] if user_request else []
    )
    invoke_input: dict[str, Any] = {
        "messages": initial_messages,
        "resume_id": source.id,
        "source_display_name": source.display_name,
        "user_request": user_request,
        "resume_session_id": f"resume:{principal_id}:{thread_id}:{tool_call_id}",
        "resume_shot": source.content,
    }

    dlog(
        "resume",
        "resume_agent_node",
        "调用 RESUME 子图",
        resume_id=resume_id,
        has_user_request=user_request is not None,
        tool_call_id=tool_call_id,
    )

    # ── 调用子图（interrupt 时透传 GraphInterrupt） ──
    try:
        result = await resume_graph.ainvoke(invoke_input, config)
    except GraphInterrupt:
        dlog(
            "resume",
            "resume_agent_node",
            "子图 interrupt，透传 GraphInterrupt（主图将挂起）",
        )
        raise

    dlog(
        "resume",
        "resume_agent_node",
        "RESUME 子图正常完成",
        result_keys=list(result.keys())
        if isinstance(result, dict)
        else type(result).__name__,
    )

    outcome = str(result.get("outcome", "discarded"))
    output_resume_id = result.get("output_resume_id")
    output_display_name = result.get("output_display_name")
    summary = (
        str(result.get("last_summary", ""))
        or user_request
        or ("简历修改已保存。" if outcome == "saved" else "用户已放弃本轮修改。")
    )
    feedback = json.dumps(
        {
            "source_resume_id": source.id,
            "output_resume_id": output_resume_id,
            "outcome": outcome,
            "summary": summary,
            "display_name": output_display_name or source.display_name,
        },
        ensure_ascii=False,
    )

    return {
        "messages": [ToolMessage(content=feedback, tool_call_id=tool_call_id)],
    }
