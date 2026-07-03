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

from typing import Any

from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.errors import GraphInterrupt
from pydantic import Field

from agent.debug import dlog, slog
from agent.state import MainState

# 惰性加载 research_graph（避免循环依赖：research_agent.graph → agent.debug
# → agent.__init__ → agent.graph → agent.tools.research_agent → research_agent.graph）。
# 实际调用时初始化，缓存后复用；模块级 import 改为惰性不影响 Studio 子图展开
# （展开发生在运行时 AST 分析，graph 实例由引用可被闭包分析发现）。
_research_graph: Any = None


def _get_research_graph() -> Any:
    """惰性获取 research 子图实例（首次调用时 import 并缓存）。"""
    global _research_graph
    if _research_graph is None:
        from research_agent.graph import graph as rg

        _research_graph = rg
    return _research_graph


@tool
def research_agent(
    gap_topic: str = Field(
        description=(
            "知识缺口主题。当 rag_agent 检索知识库发现缺口（返回未找到的内容主题）"
            "且用户已明确同意联网深研后，由你（主图 LLM）把该缺口主题提炼传入。"
            "传入后系统会生成深研大纲请你确认，确认后联网搜集资料、整理入库并重建索引。"
        )
    ),
) -> str:
    """进入自主深研模式，联网搜集资料补足知识库缺口。

    适用场景: rag_agent 返回了知识缺口（知识库中没有相关内容）且用户已明确同意
    "深研/联网搜/补资料"等。调用后系统先生成深研大纲请你确认，确认后联网多轮搜集、
    整理成 Markdown 入库并重建本地索引。深研前会检查网络连通性，不通时挂起提示用户
    挂梯子（累计 3 次失败终止）。

    **不要**在用户尚未同意时调用——应先用自然语言询问"知识库没有该内容，要不要
    联网深研补足？"，得到肯定答复后再调本工具。普通知识检索请用 ``rag_agent``。
    """
    raise RuntimeError(
        "research_agent tool 不应被 ToolNode 执行——应由 route_after_chat 拦截"
    )


async def research_agent_node(
    state: MainState, config: RunnableConfig
) -> dict[str, Any]:
    """异步 research_agent 包装节点。

    直接 ``import`` 模块级预编译的 research 子图实例（``research_graph``，编译时不带
    checkpointer → 运行时自动继承父图 checkpointer），用 ``ainvoke`` 调用。

    子图 ``interrupt()`` 时 ``ainvoke`` 抛 ``GraphInterrupt``——本节点**透传**该异常，
    让主图线程暂停在 wrapper 节点；用户下一轮的 ``Command(resume=...)`` 会经主图
    重新进入本节点，子图靠继承的父图 checkpointer 从挂起点恢复继续（而非重跑）。

    子图完成后据 ``approval_status`` 产 SystemMessage 反馈给 ``chat_node``:
    - approved: 已深研补足，新资料入库并重建索引。
    - rejected: 用户拒绝深研，请不依赖外部资料作答。
    - aborted: 深研因网络问题中止，请稍后重试。
    """
    dlog(
        "research",
        "research_agent_node",
        "调用 RESEARCH 子图（模块级实例，继承父图 checkpointer）",
    )
    slog("research", "research_agent_node", "进入节点")

    # ── 提取 gap_topic（从主图 LLM 的 tool_call 参数）与真实 tool_call_id ──
    messages = state.get("messages", [])
    gap_topic = ""
    tool_call_id = "research_agent"
    if messages:
        last = messages[-1]
        tcs = getattr(last, "tool_calls", []) or []
        for tc in tcs:
            if tc.get("name") == "research_agent":
                gap_topic = str(tc.get("args", {}).get("gap_topic", ""))
                tool_call_id = str(tc.get("id", tool_call_id))
                break

    dlog(
        "research",
        "research_agent_node",
        "调用 RESEARCH 子图",
        gap_topic=gap_topic,
        tool_call_id=tool_call_id,
    )
    slog("research", "research_agent_node", "调用 RESEARCH 子图", gap_topic=gap_topic)

    # ── 调用子图（interrupt 时透传 GraphInterrupt） ──
    research_graph = _get_research_graph()
    try:
        result = await research_graph.ainvoke({"gap_topic": gap_topic}, config)
    except GraphInterrupt:
        dlog(
            "research",
            "research_agent_node",
            "子图 interrupt，透传 GraphInterrupt（主图将挂起）",
        )
        slog("research", "research_agent_node", "子图 interrupt，透传 GraphInterrupt")
        raise

    dlog(
        "research",
        "research_agent_node",
        "RESEARCH 子图正常完成",
        result_keys=list(result.keys())
        if isinstance(result, dict)
        else type(result).__name__,
    )
    slog("research", "research_agent_node", "RESEARCH 子图正常完成")

    # ── 据 approval_status 产反馈: 一条 ToolMessage 响应主图 tool_call + 一条 SystemMessage ──
    approval = result.get("approval_status", "approved")
    gap = result.get("gap_topic", gap_topic) or gap_topic
    summary = result.get("summary", "")

    if approval == "approved":
        feedback = (
            f"【系统后台深研完成反馈】\n已通过互联网补足知识缺口「{gap}」。\n"
            f"{summary}\n注: 新资料已格式化为 Markdown 写入本地知识库, "
            f"并已重构索引（index.md + Chroma）, 后续问答可直接命中。"
        )
    elif approval == "rejected":
        feedback = f"【系统反馈】用户拒绝了关于知识缺口「{gap}」的深研申请。请在不依赖外部补充资料的情况下尽力作答。"
    else:  # aborted
        feedback = f"【系统反馈】深研「{gap}」因网络问题中止（DuckDuckGo 连续 3 次不可达）。请稍后重试, 或在不依赖外部资料的情况下作答。"

    out_msgs: list[Any] = [
        ToolMessage(content=feedback, tool_call_id=tool_call_id),
        SystemMessage(content=feedback),
    ]

    return {
        "messages": out_msgs,
        "research_output": {
            "approval_status": approval,
            "gap_topic": gap,
            "summary": summary,
            "new_file_name": result.get("new_file_name", ""),
        },
    }
