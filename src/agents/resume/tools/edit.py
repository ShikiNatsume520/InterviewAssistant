"""resume 子图的编辑引擎：grep_replace 工具 + _apply_grep_replace 纯函数。

``grep_replace`` 是 chat_node 直接调用的**确定性**工具——LLM 从 prompt 里的
``resume_shot`` 自己找出要改的片段作为 ``grep_target``，连同 ``replace_content``
传入，后端做 find→replace，**工具内无 LLM**。

设计要点（原型 ``phase9_grep_replace_probe.py`` 验证通过）：
- **唯一性校验**：``grep_target`` 必须在 ``resume_shot`` 中存在且唯一；不存在返错，
  多处命中返错并附行号帮 LLM 用更长片段重定位。
- **逐条 approve 小循环**：chat_node 一波可发多个 ``grep_replace`` tool_call，
  ``edit_executor_node`` 取一条判断 grep，命中进 ``approve_node`` 显示 diff 请用户
  approve/reject/suggest——approve 即替换 shot 取下一条，直到全部完成。**写操作
  串行**（逐条 approve，不并行）。
- **EditError 回 chat_node**：grep 不到时加 ``EditError! ... not in resume_shot``
  HumanMessage 回 chat_node，chat_node 据最新 shot 重发修正指令（新波覆盖旧波未执行）。
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import Field

# --------------------------------------------------------------------------- #
# grep_replace 工具
# --------------------------------------------------------------------------- #


@tool
def grep_replace(
    grep_target: str = Field(
        description=(
            "要在简历中定位并替换的原文片段。必须在当前草稿中**存在且唯一**——"
            "若有多处命中，请提供更长的上下文片段使其唯一定位。"
        )
    ),
    replace_content: str = Field(
        description="替换 grep_target 的新内容（可为空字符串表示删除该片段）。"
    ),
    section: str = Field(
        default="",
        description="面向用户展示的简历章节或位置，例如“项目经历”。",
    ),
    reason: str = Field(
        default="",
        description="面向用户说明这项修改为何有帮助；不得编造事实。",
    ),
) -> str:
    """在当前简历草稿中定位 grep_target 并用 replace_content 替换。

    定位要求 ``grep_target`` 在草稿中存在且唯一：
    - 不存在 → 返回错误，请重新确认目标片段
    - 多处命中 → 返回错误并附各处行号，请用更长上下文片段唯一定位
    - 唯一命中 → 执行替换

    可在一波中调用多次（系统逐条 approve，串行执行）；某个失败不影响已成功的替换。
    """
    raise RuntimeError(
        "grep_replace tool 不应被 ToolNode 执行——应由 edit_executor_node 处理"
    )


# --------------------------------------------------------------------------- #
# 核心 find→replace 逻辑（纯函数，单测友好）
# --------------------------------------------------------------------------- #


def _apply_grep_replace(
    draft: str, grep_target: str, replace_content: str
) -> tuple[str, str]:
    """单次 find→replace，唯一性校验。

    Args:
        draft: 当前草稿文本。
        grep_target: 要定位的原文片段。
        replace_content: 替换内容。

    Returns:
        ``(new_draft, error_msg)``——成功时 ``error_msg == ""`` 且 ``new_draft``
        为替换后草稿；失败时 ``new_draft == draft`` 不变、``error_msg`` 为提示。
    """
    if not grep_target:
        return draft, "grep_target 为空，请提供要替换的原文片段。"
    count = draft.count(grep_target)
    if count == 0:
        preview = grep_target[:40] + ("..." if len(grep_target) > 40 else "")
        return draft, f"未找到「{preview}」，请确认目标片段在当前草稿中存在。"
    if count > 1:
        lines = draft.splitlines()
        hits = [
            f"L{i + 1}: {ln.strip()[:50]}"
            for i, ln in enumerate(lines)
            if grep_target in ln
        ]
        preview = grep_target[:30] + ("..." if len(grep_target) > 30 else "")
        return (
            draft,
            f"「{preview}」命中 {count} 处，需更长上下文片段唯一定位: {hits}",
        )
    return draft.replace(grep_target, replace_content), ""


# --------------------------------------------------------------------------- #
# 导出
# --------------------------------------------------------------------------- #

EDIT_TOOLS: list[Any] = [grep_replace]
"""chat_node bind 的编辑工具列表。"""
