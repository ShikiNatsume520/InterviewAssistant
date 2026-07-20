"""phase8: resume_agent wrapper IO 闭环原型验证。

验证阶段1核心机制（纯 IO，无 LLM，不依赖 src 正式代码）：
1. wrapper 从 tool_call args 读 resume_file（相对/绝对路径解析）
2. 读文件 → 灌 resume_shot + init last_shot=""
3. 最小子图 init→END（手设 save）经 invoke 返回
4. wrapper 据 result.save persist：True 覆盖源文件，False 不动
5. last_shot 初始 ""
6. resume_file 缺省 → 返回错误提示（不读文件，缺口留阶段4）

通过 = 上述行为符合预期。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages


# 最小 ResumeState-like（模拟阶段1新 schema 子集） ---------------------------- #
class _State(TypedDict, total=False):
    resume_shot: str
    last_shot: str
    save: bool
    messages: Annotated[list[BaseMessage], add_messages]


def make_stub_graph(save_value: bool) -> Any:
    """最小子图 init→exit，exit 手设 save（模拟阶段3 hitl_standby 退出设值）。"""
    g = StateGraph(_State)

    def init_node(state: _State) -> dict[str, Any]:
        return {"last_shot": ""}

    def exit_node(state: _State) -> dict[str, Any]:
        return {"save": save_value}

    g.add_node("init", init_node)
    g.add_node("exit", exit_node)
    g.set_entry_point("init")
    g.add_edge("init", "exit")
    g.add_edge("exit", END)
    return g.compile()


# 模拟 wrapper IO 逻辑（阶段1正式 wrapper 的核心） ---------------------------- #
RESUMES_DIR_NAME = "resumes"


def _resolve_resume_path(resume_file: str, resumes_dir: Path) -> Path:
    p = Path(resume_file)
    return p if p.is_absolute() else resumes_dir / resume_file


def wrapper_io_persist(
    tool_call: dict, resumes_dir: Path, save_value: bool
) -> dict:
    """模拟 wrapper：读文件灌入 → 调子图 → 据 save persist。"""
    args = tool_call.get("args", {}) or {}
    resume_file = str(args.get("resume_file", ""))
    if not resume_file:
        return {"error": "未指定 resume_file，请先指定要修改的简历文件"}
    src = _resolve_resume_path(resume_file, resumes_dir)
    if not src.is_file():
        return {"error": f"简历文件不存在: {src}"}
    resume_shot = src.read_text(encoding="utf-8")
    graph = make_stub_graph(save_value)
    result = graph.invoke({"resume_shot": resume_shot, "last_shot": ""})
    save = bool(result.get("save"))
    if save:
        src.write_text(str(result.get("resume_shot", "")), encoding="utf-8")
    return {
        "save": save,
        "persisted": save,
        "last_shot": result.get("last_shot"),
        "resume_shot_len": len(str(result.get("resume_shot", ""))),
    }


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    resumes = tmp / RESUMES_DIR_NAME
    resumes.mkdir()
    resume_file = resumes / "test.md"
    original = "# 测试简历\n\n## 教育经历\n内容A\n"
    resume_file.write_text(original, encoding="utf-8")

    # 1. 缺省 resume_file → 错误
    r = wrapper_io_persist({"name": "resume_agent", "args": {}}, resumes, True)
    assert "error" in r, f"缺省应报错, got {r}"
    print("[1] 缺省 resume_file 报错: OK")

    # 2. save=True → 覆盖源文件 + last_shot=""
    r = wrapper_io_persist(
        {"name": "resume_agent", "args": {"resume_file": "test.md"}}, resumes, True
    )
    assert r["save"] is True and r["persisted"] is True, r
    assert r["last_shot"] == "", r
    assert resume_file.read_text(encoding="utf-8") == original
    print("[2] save=True persist 覆盖 + last_shot='': OK")

    # 3. save=False → 源文件不动
    r = wrapper_io_persist(
        {"name": "resume_agent", "args": {"resume_file": "test.md"}}, resumes, False
    )
    assert r["save"] is False and r["persisted"] is False, r
    print("[3] save=False 不 persist: OK")

    # 4. 绝对路径解析
    r = wrapper_io_persist(
        {"name": "resume_agent", "args": {"resume_file": str(resume_file)}},
        resumes,
        True,
    )
    assert "error" not in r, r
    print("[4] 绝对路径解析: OK")

    # 5. 不存在文件 → 错误
    r = wrapper_io_persist(
        {"name": "resume_agent", "args": {"resume_file": "nope.md"}}, resumes, True
    )
    assert "error" in r, r
    print("[5] 不存在文件报错: OK")

    print("\nALL PASSED")


if __name__ == "__main__":
    main()
