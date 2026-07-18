"""research_agent 子智能体：Tool 定义 + 包装节点函数。

与 ``rag_agent`` / ``resume_agent`` 的集成方式完全一致:

- ``research_agent``（``@tool`` 装饰）：供 LLM ``bind_tools`` 的 Tool 对象。
- ``research_agent_node``：异步 LangGraph 节点函数。直接 ``import`` 模块级预编译的
  research 子图实例（``research_agent.graph.graph``，编译时不带 checkpointer → 运行时
  自动继承父图 checkpointer），用 ``ainvoke`` 调用。

子图作为模块级实例被 wrapper 函数体直接引用，``find_subgraph_pregel`` 的 AST
闭包分析可发现它 → ``PregelNode.subgraphs`` 被填充 → Studio 可展开子图内部节点
（原型 ``phase5_inherit_cp_probe.py`` 验证通过）。

research 子图内部用 ``interrupt()`` 挂起（outline_confirm + connectivity_check）。
子图 ainvoke 会抛 ``GraphInterrupt``，wrapper 透传该异常让主图线程暂停；下一轮主图
``Command(resume=...)`` 会精准恢复到子图挂起点（原型
``phase6_subgraph_interrupt_probe.py`` 验证通过）。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.errors import GraphInterrupt
from pydantic import Field

from agents.research.graph import graph as research_graph
from kernel.logging import dlog


@tool
def research_agent(
    gap_topic: str = Field(
        description=(
            "知识缺口主题。当 rag_agent 检索知识库发现缺口（返回未找到的内容主题）"
            "且用户已明确同意联网深研后，由你（主图 LLM）把该缺口主题提炼传入。"
            "传入后系统会生成深研大纲请用户确认，确认后联网搜集资料并生成报告。"
        )
    ),
) -> str:
    """进入自主深研模式，联网搜集资料补足知识库缺口。

    适用场景: rag_agent 返回了知识缺口（知识库中没有相关内容）且用户已明确同意
    "深研/联网搜/补资料"等。调用后系统先生成深研大纲请用户确认，确认后联网多轮搜集、
    整理成报告并询问用户是否授权加入个人知识库。实际知识库导入由后续导入服务负责。
    深研前会检查网络连通性，不通时挂起提示用户
    挂梯子（累计 3 次失败终止）。

    **不要**在用户尚未同意时调用——应先用自然语言询问"知识库没有该内容，要不要
    联网深研补足？"，得到肯定答复后再调本工具。普通知识检索请用 ``rag_agent``。
    """
    raise RuntimeError(
        "research_agent tool 不应被 ToolNode 执行——应由 route_after_chat 拦截"
    )


async def research_agent_node(
    state: dict[str, Any], config: RunnableConfig
) -> dict[str, Any]:
    """异步 research_agent 包装节点。

    直接 ``import`` 模块级预编译的 research 子图实例（``research_graph``，编译时不带
    checkpointer → 运行时自动继承父图 checkpointer），用 ``ainvoke`` 调用。

    子图 ``interrupt()`` 时 ``ainvoke`` 抛 ``GraphInterrupt``——本节点**透传**该异常，
    让主图线程暂停在 wrapper 节点；用户下一轮的 ``Command(resume=...)`` 会经主图
    重新进入本节点，子图靠继承的父图 checkpointer 从挂起点恢复继续（而非重跑）。

    子图完成后据 ``approval_status`` 产精简 ToolMessage 反馈给 ``chat_node``:
    - approved: 研究完成；报告已由 Research Agent 直接展示，ToolMessage 不携带全文。
    - rejected: 用户拒绝深研，请不依赖外部资料作答。
    - aborted: 深研因网络问题中止，请稍后重试。
    """
    dlog("research", "research_agent_node", "进入节点")

    # ── 提取 gap_topic（从 Send arg 的 tool_call）与真实 tool_call_id ──
    tc: dict[str, Any] = state.get("tool_call", {}) or {}
    args = tc.get("args", {}) or {}
    gap_topic = str(args.get("gap_topic", ""))
    tool_call_id = str(tc.get("id", "research_agent"))

    dlog(
        "research",
        "research_agent_node",
        "调用 RESEARCH 子图",
        gap_topic=gap_topic,
        tool_call_id=tool_call_id,
    )

    # ── 调用子图（interrupt 时透传 GraphInterrupt） ──
    try:
        configurable = config.get("configurable", {})
        result = await research_graph.ainvoke(
            {
                "gap_topic": gap_topic,
                "principal_id": str(configurable.get("user_id", "")),
                "thread_id": str(configurable.get("thread_id", "")),
                "tool_call_id": tool_call_id,
            },
            config,
        )
    except GraphInterrupt:
        dlog(
            "research",
            "research_agent_node",
            "子图 interrupt，透传 GraphInterrupt（主图将挂起）",
        )
        raise

    dlog(
        "research",
        "research_agent_node",
        "RESEARCH 子图正常完成",
        result_keys=list(result.keys())
        if isinstance(result, dict)
        else type(result).__name__,
    )

    # 完整报告不进入 MainState.messages，避免后续每轮对话反复携带知识正文。
    approval = result.get("approval_status", "approved")
    gap = result.get("gap_topic", gap_topic) or gap_topic
    summary = str(result.get("report_summary", ""))
    sources = result.get("sources", [])
    source_count = sum(
        isinstance(source, dict) and source.get("status") == "succeeded"
        for source in sources
    )
    if approval == "approved":
        outcome = "completed"
        requirements = [
            "完整报告已经由 Research Agent 在聊天区展示，不要重复生成报告全文。",
            "可以简短确认研究已经完成。",
        ]
    elif approval == "rejected":
        outcome = "cancelled"
        requirements = ["告知用户已取消本次深度研究。", "不得虚构研究结论。"]
    else:
        outcome = "aborted"
        requirements = ["说明研究因网络问题中止。", "可以建议用户稍后重试。"]

    knowledge_decision = str(result.get("knowledge_decision", "not_asked"))
    import_status = str(result.get("import_status", "not_requested"))
    knowledge_resource_id = str(result.get("knowledge_resource_id", ""))
    if import_status == "completed":
        requirements.append("可以明确说明用户已批准，报告已进入个人知识库。")
    elif knowledge_decision == "approved":
        requirements.append("个人知识库导入失败，不得声称已经入库；可提示用户稍后重试。")
    else:
        requirements.append("不得声称资料已经进入知识库。")
    tool_result = {
        "type": "research_result",
        "outcome": outcome,
        "topic": gap,
        "summary": summary,
        "source_count": source_count,
        "knowledge_base": {
            "decision": knowledge_decision,
            "import_status": import_status,
            "resource_id": knowledge_resource_id,
        },
        "response_requirements": requirements,
    }

    return {
        "messages": [
            ToolMessage(
                content=json.dumps(tool_result, ensure_ascii=False),
                tool_call_id=tool_call_id,
                name="research_agent",
            )
        ],
        "research_output": {
            **tool_result,
            "approval_status": approval,
            "gap_topic": gap,
        },
    }
