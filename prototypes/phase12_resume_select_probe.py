"""phase12: resume 子图 select_resume 头部 + persist 尾部 + wrapper 透传闭环原型。

验证阶段4新方案（职责重划：子图自包含读写，wrapper 退化为纯透传）：
1. select_resume_node interrupt：列 data/resumes/*.md + 空模板选项
2. 前端选文件 → 读文件灌 resume_shot + 存 resume_file
3. 空模板分支：选 empty → resume_file=None + 灌骨架
4. chat_node(stub edit) → edit_executor → approve → finish → hitl(exit save)
5. save=True → persist_node 写源文件 → END；save=False → 直接 END
6. persist 遇 resume_file=None + save=True → 生成新文件名写入

stub chat_node（不调真 LLM，手设 grep_replace）。wrapper 透传：只传 intent→HumanMessage。
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt

from kernel.paths import RESUMES_DIR


class _State(TypedDict, total=False):
    resume_shot: str
    resume_file: str  # 选定文件名（相对 RESUMES_DIR）；空模板时为 ""
    last_shot: str
    save: bool
    last_summary: str
    chat_step: int  # stub chat_node 计数（正式用真 LLM 不需要）
    messages: Annotated[list[BaseMessage], add_messages]


_EMPTY_TEMPLATE = "# 姓名\n\n## 教育经历\n\n## 工作经历\n\n## 技能\n"


def _list_resumes() -> list[str]:
    """列 data/resumes/ 下 *.md 文件名。"""
    if not RESUMES_DIR.is_dir():
        return []
    return sorted(p.name for p in RESUMES_DIR.glob("*.md"))


# --------------------------------------------------------------------------- #
# select_resume_node：头部 interrupt 选简历
# --------------------------------------------------------------------------- #


def select_resume_node(state: _State) -> dict[str, Any]:
    files = _list_resumes()
    value = interrupt({"phase": "resume_select", "files": files, "empty_option": True})
    dlog_stub("select_resume", "收到选择", value=value)
    if isinstance(value, dict):
        chosen = str(value.get("resume_file", ""))
    else:
        chosen = str(value)
    if chosen == "empty" or not chosen:
        return {"resume_shot": _EMPTY_TEMPLATE, "resume_file": ""}
    src = RESUMES_DIR / chosen
    if not src.is_file():
        return {"resume_shot": _EMPTY_TEMPLATE, "resume_file": ""}
    return {"resume_shot": src.read_text(encoding="utf-8"), "resume_file": chosen}


# --------------------------------------------------------------------------- #
# init / chat_node(stub) / edit / approve / hitl / persist
# --------------------------------------------------------------------------- #


def init_node(state: _State) -> dict[str, Any]:
    return {"last_shot": ""}


def chat_node(state: _State) -> dict[str, Any]:
    """stub：首轮流 edit（grep_replace 改「八爪科技大学」→「测试科技大学」），次轮流 finish。

    用 sample.md（lapis-cv 模板）里真实存在的片段，顺带验证 grep_replace 能在
    HTML+md 混排结构里唯一命中。
    """
    step = state.get("chat_step", 0)
    if step == 0:
        msg = AIMessage(
            content="",
            tool_calls=[{
                "id": "e0",
                "name": "grep_replace",
                "args": {"grep_target": "八爪科技大学", "replace_content": "测试科技大学"},
            }],
        )
        return {"messages": [msg], "chat_step": step + 1}
    return {
        "messages": [AIMessage(content="完成总结")],
        "last_summary": "完成总结",
        "chat_step": step + 1,
    }


def route_after_chat(state: _State) -> str:
    msgs = state.get("messages", [])
    if not msgs:
        return "hitl_standby"
    last = msgs[-1]
    tcs = getattr(last, "tool_calls", []) or []
    if not tcs:
        return "hitl_standby"
    return "edit_executor" if tcs[0].get("name") == "grep_replace" else "hitl_standby"


def edit_executor_node(state: _State) -> dict[str, Any]:
    ai = state.get("messages", [])[-1]
    draft = state.get("resume_shot", "")
    last_shot = draft
    for tc in getattr(ai, "tool_calls", []) or []:
        args = tc.get("args", {}) or {}
        target = str(args.get("grep_target", ""))
        rep = str(args.get("replace_content", ""))
        if target and draft.count(target) == 1:
            draft = draft.replace(target, rep)
    return {"resume_shot": draft, "last_shot": last_shot}


def approve_node(state: _State) -> dict[str, Any]:
    value = interrupt({"phase": "approve", "before": state.get("last_shot", ""), "after": state.get("resume_shot", "")})
    print(f"  [approve] value={value}")
    if isinstance(value, dict) and value.get("decision") == "reject":
        return {"resume_shot": state.get("last_shot", "")}
    return {}


def hitl_standby_node(state: _State) -> dict[str, Any]:
    value = interrupt({"phase": "hitl", "summary": state.get("last_summary", "")})
    print(f"  [hitl] value={value}")
    if isinstance(value, dict) and value.get("action") == "exit":
        ret = {"save": bool(value.get("save", False))}
        print(f"  [hitl] returning {ret}")
        return ret
    return {"messages": [HumanMessage(content=str(value.get("request", "")) if isinstance(value, dict) else str(value))]}


def route_after_hitl(state: _State) -> str:
    msgs = state.get("messages", [])
    last_is_human = bool(msgs) and isinstance(msgs[-1], HumanMessage)
    save_val = state.get("save")
    print(f"  [route_after_hitl] last_is_human={last_is_human} save={save_val!r}")
    if last_is_human:
        return "chat_node"
    if save_val:
        return "persist"
    return END


def persist_node(state: _State) -> dict[str, Any]:
    """写源文件：resume_file 非空覆盖原文件，为空生成新文件名。"""
    resume_file = state.get("resume_file", "")
    shot = state.get("resume_shot", "")
    if resume_file:
        (RESUMES_DIR / resume_file).write_text(shot, encoding="utf-8")
        dlog_stub("persist", "覆盖源文件", file=resume_file)
    else:
        # 空模板 save=True：生成新文件名写入（原型用固定名避免时间依赖）
        new_name = "new_resume_prototype.md"
        (RESUMES_DIR / new_name).write_text(shot, encoding="utf-8")
        dlog_stub("persist", "新建文件", file=new_name)
    return {}


def dlog_stub(node: str, msg: str, **kw: Any) -> None:
    print(f"  [{node}] {msg} {kw}")


def build_graph() -> Any:
    wf = StateGraph(_State)
    wf.add_node("select_resume", select_resume_node)
    wf.add_node("init", init_node)
    wf.add_node("chat_node", chat_node)
    wf.add_node("edit_executor", edit_executor_node)
    wf.add_node("approve_node", approve_node)
    wf.add_node("hitl_standby", hitl_standby_node)
    wf.add_node("persist", persist_node)
    wf.set_entry_point("select_resume")
    wf.add_edge("select_resume", "init")
    wf.add_edge("init", "chat_node")
    wf.add_conditional_edges("chat_node", route_after_chat, {
        "edit_executor": "edit_executor", "hitl_standby": "hitl_standby",
    })
    wf.add_edge("edit_executor", "approve_node")
    wf.add_edge("approve_node", "chat_node")
    wf.add_conditional_edges("hitl_standby", route_after_hitl, {
        "chat_node": "chat_node", "persist": "persist", END: END,
    })
    wf.add_edge("persist", END)
    return wf.compile(checkpointer=MemorySaver())


def run_flow(g: Any, intent: str, resumes: list[Any], tid: str) -> dict[str, Any]:
    """wrapper 透传：只传 intent→HumanMessage，按序 resume。"""
    config: dict[str, Any] = {"configurable": {"thread_id": tid}}
    g.invoke({"messages": [HumanMessage(content=intent)]}, config)
    for r in resumes:
        g.invoke(Command(resume=r), config)
    return dict(g.get_state(config).values)


def main() -> None:
    g = build_graph()
    sample_path = RESUMES_DIR / "sample.md"
    original_sample = sample_path.read_text(encoding="utf-8") if sample_path.is_file() else ""

    # 1. 选 sample.md → edit → approve(approve) → finish → exit(save=True) → persist 覆盖
    print("=== S1: 选 sample.md, save=True, persist 覆盖 ===")
    vals = run_flow(
        g, "把八爪科技大学改成测试科技大学",
        [
            {"resume_file": "sample.md"},   # select_resume
            {"decision": "approve"},         # approve
            {"action": "exit", "save": True},  # hitl
        ],
        tid="s1",
    )
    assert "测试科技大学" in vals["resume_shot"], vals["resume_shot"]
    print(f"  [debug] save={vals.get('save')!r} keys={list(vals.keys())}")
    assert vals.get("save") is True
    written = sample_path.read_text(encoding="utf-8")
    assert "测试科技大学" in written, "persist 应覆盖 sample.md"
    assert "八爪科技大学" not in written
    print(f"  sample.md 已被 persist 覆盖（含测试科技大学）: OK")
    # 还原 sample.md 供后续场景
    sample_path.write_text(original_sample, encoding="utf-8")

    # 2. 选 sample.md, save=False → 不 persist，源文件不动
    print("=== S2: 选 sample.md, save=False, 不 persist ===")
    vals = run_flow(
        g, "把八爪科技大学改成测试科技大学",
        [
            {"resume_file": "sample.md"},
            {"decision": "approve"},
            {"action": "exit", "save": False},
        ],
        tid="s2",
    )
    assert "测试科技大学" in vals["resume_shot"]  # 子图内改了
    unchanged = sample_path.read_text(encoding="utf-8")
    assert "八爪科技大学" in unchanged and "测试科技大学" not in unchanged, "save=False 源文件不应变"
    print("  save=False 源文件不动: OK")

    # 3. 选 empty（空模板）→ save=True → persist 新建文件
    print("=== S3: 选 empty(空模板), save=True, persist 新建 ===")
    new_path = RESUMES_DIR / "new_resume_prototype.md"
    if new_path.exists():
        new_path.unlink()
    vals = run_flow(
        g, "新建一份简历",
        [
            {"resume_file": "empty"},  # select_resume 选空模板
            {"decision": "approve"},
            {"action": "exit", "save": True},
        ],
        tid="s3",
    )
    assert vals["resume_file"] == "", "空模板 resume_file 应为空"
    assert new_path.exists(), "persist 应新建 new_resume_prototype.md"
    print(f"  空模板 save=True 新建文件: OK")
    new_path.unlink()  # 清理

    print("\nALL PASSED")


if __name__ == "__main__":
    main()
