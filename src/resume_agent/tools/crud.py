"""resume_agent 的简历草稿 CRUD 工具（section 级）。

工具以 ``## 标题`` 为操作单元。通过 ``ToolRuntime`` 注入读取图状态中的
``current_draft``，返回 ``Command(update=...)`` 把新草稿写回 state——
ToolNode 的 ``_normalize_tool_response`` 严格要求工具返回值是
``Command | ToolMessage | list``，**裸 dict 不被接受**（原型验证发现）。

每个工具返回的 ``Command.update`` 同时写入：
- ``current_draft``：修改后的草稿；
- ``last_draft``：修改前的快照（供 ``step_confirm_node`` 拒绝时恢复）；
- ``messages``：``ToolMessage``（带真实 ``tool_call_id``），告知 LLM 操作结果。
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command

# --------------------------------------------------------------------------- #
# 草稿解析工具
# --------------------------------------------------------------------------- #

_SECTION_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")
"""匹配 markdown 二级标题行（``## 标题``）。"""


def _split_sections(draft: str) -> list[tuple[str, str]]:
    """把草稿按二级标题切成 ``[(title, body), ...]``。

    标题行之前的 preamble（如 ``# 我的简历`` 一级标题）归为首段，
    title 记为 ``""``。
    """
    if not draft:
        return []
    lines = draft.splitlines()
    sections: list[tuple[str, str]] = []
    buf: list[str] = []
    cur_title = ""
    for ln in lines:
        m = _SECTION_RE.match(ln)
        if m:
            if buf or cur_title == "":
                sections.append((cur_title, "\n".join(buf)))
            cur_title = m.group(1).strip()
            buf = []
        else:
            buf.append(ln)
    sections.append((cur_title, "\n".join(buf)))
    return [s for s in sections if s[1].strip() or s[0] == ""]


def _join_sections(sections: list[tuple[str, str]]) -> str:
    """把 ``[(title, body), ...]`` 重新拼成草稿文本。"""
    parts: list[str] = []
    for title, body in sections:
        if title:
            parts.append(f"## {title}\n{body}")
        else:
            parts.append(body)
    return "\n".join(parts).strip() + "\n"


# --------------------------------------------------------------------------- #
# CRUD 工具
# --------------------------------------------------------------------------- #


def _make_update(
    runtime: ToolRuntime, new_draft: str, msg: str
) -> Command[Any]:
    """构造写回 state 的 ``Command``（含 last_draft 快照 + ToolMessage）。"""
    state: dict[str, Any] = dict(runtime.state)
    old = str(state.get("current_draft", ""))
    return Command(
        update={
            "current_draft": new_draft,
            "last_draft": old,
            "messages": [
                ToolMessage(content=msg, tool_call_id=runtime.tool_call_id)
            ],
        }
    )


@tool
def add_section(
    runtime: ToolRuntime, title: str, content: str
) -> Command[Any]:
    """向简历追加一个新章节。

    Args:
        title: 章节标题（不带 ``##`` 前缀）。
        content: 章节正文。

    Returns:
        ``Command`` 写回草稿 + ToolMessage。
    """
    draft: str = runtime.state.get("current_draft", "")
    if any(title == t for t, _ in _split_sections(draft)):
        return _make_update(runtime, draft, f"章节「{title}」已存在，未改动。")
    new_draft = (draft.rstrip() + f"\n\n## {title}\n{content}\n").strip() + "\n"
    return _make_update(runtime, new_draft, f"已添加章节「{title}」。")


@tool
def update_section(
    runtime: ToolRuntime, title: str, new_content: str
) -> Command[Any]:
    """替换指定章节的正文。

    Args:
        title: 要修改的章节标题。
        new_content: 新的章节正文。

    Returns:
        ``Command`` 写回草稿 + ToolMessage。
    """
    draft: str = runtime.state.get("current_draft", "")
    sections = _split_sections(draft)
    found = False
    for i, (t, _) in enumerate(sections):
        if t == title:
            sections[i] = (t, new_content)
            found = True
            break
    if not found:
        return _make_update(runtime, draft, f"未找到章节「{title}」，未改动。")
    new_draft = _join_sections(sections)
    return _make_update(runtime, new_draft, f"已更新章节「{title}」。")


@tool
def delete_section(runtime: ToolRuntime, title: str) -> Command[Any]:
    """删除指定章节（含其正文）。

    Args:
        title: 要删除的章节标题。

    Returns:
        ``Command`` 写回草稿 + ToolMessage。
    """
    draft: str = runtime.state.get("current_draft", "")
    sections = _split_sections(draft)
    new_sections = [(t, b) for t, b in sections if t != title]
    if len(new_sections) == len(sections):
        return _make_update(runtime, draft, f"未找到章节「{title}」，未改动。")
    new_draft = _join_sections(new_sections)
    return _make_update(runtime, new_draft, f"已删除章节「{title}」。")


@tool
def reorder_sections(
    runtime: ToolRuntime, new_order: list[str]
) -> Command[Any]:
    """按给定顺序重排现有章节。

    Args:
        new_order: 章节标题的新顺序（必须包含全部现有章节）。

    Returns:
        ``Command`` 写回草稿 + ToolMessage。
    """
    draft: str = runtime.state.get("current_draft", "")
    sections = {t: b for t, b in _split_sections(draft) if t}
    if set(new_order) != set(sections):
        missing = set(sections) - set(new_order)
        extra = set(new_order) - set(sections)
        detail = []
        if missing:
            detail.append(f"缺失: {missing}")
        if extra:
            detail.append(f"多余: {extra}")
        return _make_update(
            runtime, draft, f"顺序与现有章节不符（{'；'.join(detail)}），未改动。"
        )
    rebuilt = [(t, sections[t]) for t in new_order]
    preamble = next((b for t, b in _split_sections(draft) if t == ""), "")
    new_sections: list[tuple[str, str]] = []
    if preamble.strip():
        new_sections.append(("", preamble))
    new_sections.extend(rebuilt)
    new_draft = _join_sections(new_sections)
    return _make_update(runtime, new_draft, f"已重排为: {' → '.join(new_order)}。")


@tool
def show_draft(runtime: ToolRuntime) -> Command[Any]:
    """查看当前草稿全文（不修改）。"""
    draft: str = runtime.state.get("current_draft", "")
    return _make_update(runtime, draft, f"当前草稿:\n{draft}")


# --------------------------------------------------------------------------- #
# 工具列表导出
# --------------------------------------------------------------------------- #

RESUME_CRUD_TOOLS: list[Any] = [
    add_section,
    update_section,
    delete_section,
    reorder_sections,
    show_draft,
]
"""resume 子图 react_router 可用的 section 级 CRUD 工具列表。"""
