"""resume_agent 子智能体：Tool 定义 + 包装节点函数。

阶段4（常驻编辑会话改造完成）：wrapper 退化为**纯透传**——子图自包含读写
（``select_resume_node`` 选读、``persist_node`` 写回），wrapper 不碰文件 IO。

职责：
- 从主图 tool_call args 读 ``intent``（主图精炼的修改意图）。
- 灌 ``messages=[HumanMessage(intent)]`` 作为首条用户请求（决策 A：首条请求统一
  为 HumanMessage，resume chat_node 从 messages 读需求；前端直连时由前端传用户输入）。
- 透传 ``selection``（可选，前端直连唤醒时带；主图激活路径无选区）。
- ``ainvoke`` resume 子图（子图头部 select_resume 自己选简历、尾部 persist 自己写回）。
- 子图完成后据 ``result.last_summary`` 产反馈 ``ToolMessage`` + ``SystemMessage``，
  并回填 ``current_resume``（最终草稿）。

简历选择（读哪份）由子图头部 ``select_resume_node`` 经 interrupt 让前端选，
**子图/LLM 不碰路径解析**——主图只传 intent，不传 resume_file。

与 ``rag_agent`` 的集成方式一致：

- ``resume_agent``（``@tool`` 装饰）：供 LLM ``bind_tools`` 的 Tool 对象。
- ``resume_agent_node``：异步 LangGraph 节点函数。直接 ``import`` 模块级预编译的
  resume 子图实例（``resume_agent.graph.graph``，编译时不带 checkpointer → 运行时
  自动继承父图 checkpointer），用 ``ainvoke`` 调用。

子图作为模块级实例被 wrapper 函数体直接引用，``find_subgraph_pregel`` 的 AST
闭包分析可发现它 → ``PregelNode.subgraphs`` 被填充 → Studio 可展开子图内部节点。

resume 子图内部用 ``interrupt()`` 挂起（select_resume / plan_confirm / approve /
hitl_standby）。子图 ainvoke 会抛 ``GraphInterrupt``，wrapper 必须透传该异常让主图
线程也暂停；下一轮主图 ``Command(resume=...)`` 会精准恢复到子图挂起点（checkpointer
由父图继承，子图状态持久化在共享命名空间）。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.errors import GraphInterrupt
from pydantic import Field

from agents.resume.graph import graph as resume_graph
from kernel.logging import dlog


@tool
def resume_agent(
    intent: str = Field(
        description=(
            "用户修改简历的需求描述。由你（主图 LLM）据上下文把用户的简历修改消息"
            "整理扩充成更有条理的优化 query（例如把「把学校改一下」整理成「用户希望"
            "修改教育经历中的校名」），而非详细计划。不要塞入整段简历——具体改哪份"
            "简历由系统进入简历优化后请用户选择。"
        )
    ),
) -> str:
    """进入简历优化模式，按用户意图对简历进行多轮修改并请你确认。

    适用场景：用户明确要求"优化简历""改简历""补充项目经历"等修改简历的意图。
    调用后系统会进入常驻编辑会话：先请你选择要修改的简历，然后你可制定修改计划
    请用户确认，或直接做修改后请用户批准；完成前会一直停留在会话中，直到前端发送
    结束信号（保存或放弃）。

    不要在普通知识问答场景调用此工具——知识检索请用 ``rag_agent``。
    """
    raise RuntimeError(
        "resume_agent tool 不应被 ToolNode 执行——应由 route_after_chat 拦截"
    )


async def resume_agent_node(
    state: dict[str, Any], config: RunnableConfig
) -> dict[str, Any]:
    """异步 resume_agent 包装节点（纯透传）。

    由 ``route_after_chat`` 经 ``Send("resume_agent", {"tool_call": tc})`` 调用，
    从 Send arg 读取本节点负责的单个 tool_call（含 ``intent`` / ``id``）。

    流程：
    1. 从 args 读 ``intent``（主图精炼的 query）→ 包成首条 HumanMessage 灌入 messages，
       同时把 ``intent`` 灌入子图 state——select_resume 的 interrupt payload 据此带出
       给主页，主页跳转 /resume 时走 URL 传前端作右栏首条消息。
    2. ``ainvoke`` resume 子图——子图头部 ``select_resume`` 等前端回传简历、尾部
       ``persist`` 不写文件（最终 shot 经 done 回复返前端），wrapper 不碰文件 IO。
    3. 据 ``result.last_summary`` 产反馈 ToolMessage + SystemMessage，回填
       ``current_resume``（最终草稿）。
    """
    dlog("resume", "resume_agent_node", "进入节点")

    tc: dict[str, Any] = state.get("tool_call", {}) or {}
    args = tc.get("args", {}) or {}
    intent = str(args.get("intent", ""))
    tool_call_id = str(tc.get("id", "resume_agent"))

    # 首条用户请求：主图激活时用 intent 生成 HumanMessage（决策 A）。同时把 intent
    # 灌入子图 state——select_resume_node 的 interrupt payload 据此带出给主页，
    # 主页跳转 /resume 时走 URL 传前端作右栏首条消息。
    initial_messages: list[Any] = [HumanMessage(content=intent)] if intent else []
    invoke_input: dict[str, Any] = {
        "messages": initial_messages,
        "intent": intent,
    }

    dlog(
        "resume",
        "resume_agent_node",
        "调用 RESUME 子图",
        intent=intent,
        tool_call_id=tool_call_id,
    )

    # ── 调用子图（interrupt 时透传 GraphInterrupt） ──
    # resume_shot / resume_file 由子图内部 select_resume/persist 管理，wrapper 不灌。
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

    final_shot = str(result.get("resume_shot", ""))
    summary = str(result.get("last_summary", "")) or intent or "简历优化完成。"
    feedback = f"【系统反馈】{summary}"
    out_msgs: list[Any] = [
        ToolMessage(content=feedback, tool_call_id=tool_call_id),
        SystemMessage(content=feedback),
    ]

    return {
        "messages": out_msgs,
        "current_resume": final_shot,
    }
