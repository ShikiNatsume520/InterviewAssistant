"""resume 编辑引擎单测：_apply_grep_replace 唯一性校验。

逐条 approve 小循环改造后，edit_executor 纯函数已删（逻辑移入 graph.py 的
edit_executor_node / approve_node，靠 interrupt 驱动，单测改由联调覆盖）。这里
只测 ``_apply_grep_replace`` 纯函数（grep 命中/唯一性/空值/删除）。
"""

from __future__ import annotations

from agents.resume.tools.edit import _apply_grep_replace

_DRAFT = "## 教育经历\n学校A\n## 工作经历\n公司B\n## 项目经历\n项目C\n"


def test_grep_replace_unique_hit_replaces() -> None:
    """唯一命中 → 替换，无错误。"""
    new, err = _apply_grep_replace(_DRAFT, "学校A", "大学X")
    assert err == ""
    assert new == _DRAFT.replace("学校A", "大学X")


def test_grep_replace_not_found_keeps_draft() -> None:
    """不存在 → 返错，草稿不变。"""
    new, err = _apply_grep_replace(_DRAFT, "不存在X", "Y")
    assert err != ""
    assert new == _DRAFT


def test_grep_replace_multiple_hits_reports_lines() -> None:
    """多处命中 → 返错+行号，草稿不变。"""
    new, err = _apply_grep_replace(_DRAFT, "## ", "### ")
    assert "命中 3 处" in err
    assert new == _DRAFT


def test_grep_replace_empty_target_keeps_draft() -> None:
    """空 grep_target → 返错，草稿不变。"""
    new, err = _apply_grep_replace(_DRAFT, "", "Y")
    assert err != ""
    assert new == _DRAFT


def test_grep_replace_empty_replace_deletes() -> None:
    """replace_content 为空 → 删除该片段。"""
    new, err = _apply_grep_replace(_DRAFT, "学校A", "")
    assert err == ""
    assert "学校A" not in new
