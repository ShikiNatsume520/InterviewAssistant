"""简历优化子图的状态与数据结构定义。

常驻编辑会话：``ResumeState`` 操作 ``resume_shot``（markdown 字符串），以 chat_node
为唯一 LLM 决策点。edit_executor + approve_node 逐条小循环：chat_node 一波发多个
grep_replace，edit_executor 取一条判断 grep，命中则 approve_node 显示 diff 请用户
approve/reject/suggest，approve 即替换 shot 并取下一条，直到全部完成。

子图**无长期记忆**：每次进入状态全重置（wrapper 用主图 thread 调用，interrupt
期间状态在主图 checkpoint 的子图命名空间持久化）。
"""

from __future__ import annotations

from typing import Annotated

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class ResumeState(TypedDict, total=False):
    """简历优化子图的图状态。

    输入层（由主图 wrapper 灌入）：
        intent: 主图 chat_node 精炼的修改 query（更有条理的优化意图）。
            wrapper 灌入，select_resume_node 的 interrupt payload 据此带出给主页，
            主页跳转 /resume 时走 URL 传前端作右栏首条消息。
        resume_shot: 当前简历文本（markdown 字符串）。由前端经 select_resume_node
            的 interrupt 回传，wrapper 不灌此字段。approve 命中后即时替换，chat_node
            每次都能看到最新 shot（即便半波出错退出，已批准的已替换）。
        resume_file: 选定的简历文件名。由前端经 select_resume_node 回传，persist
            时不写文件（最终 shot 经 done 回复返前端）。

    运行期：
        plan: plan_node 产出的修改计划（步骤列表），作 chat_node 决策参考。
        last_summary: chat_node 无 tool_call 时的总结文本，hitl_standby 展示并
            退出时回传主图。
        save: 退出意图。hitl_standby 收到前端结束信号时设置——``True`` 保存退出，
            ``False`` 放弃。``route_after_hitl`` 据此决定走 ``persist_node`` 还是 END。
        processed_edits: 已处理的 grep_replace ``tool_call_id`` 列表。edit_executor /
            approve_node 据此跳过已处理条目，逐个推进小循环。chat_node 新发一波指令
            是新 AIMessage（新 tool_call_id），自动覆盖旧波未执行的——edit_executor
            只从最近带 grep_replace 的 AIMessage 提取，旧波不再处理。
        approve_decision: approve_node 的路由信号（``"approve"`` / ``"reject"`` /
            ``"suggest"``），``route_after_approve`` 据此分流。
        messages: 对话消息历史（含首条用户 HumanMessage + chat_node AIMessage +
            EditError / user refuse / 修改完毕 等 HumanMessage）。

    chat_node 文本**不存 state**——流式范式下由 ``astream(subgraphs=True)`` 实时
    推前端 token（ns=resume_agent），interrupt payload 不带 LLM 文本。
    """

    intent: str
    resume_shot: str
    resume_file: str
    plan: list[str]
    last_summary: str
    save: bool
    processed_edits: list[str]
    approve_decision: str
    messages: Annotated[list[BaseMessage], add_messages]
