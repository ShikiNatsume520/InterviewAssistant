"""phase9: grep_replace 工具 + edit_executor 串行执行原型验证。

验证阶段2核心机制（纯逻辑，无 LLM）：
1. grep_replace 唯一性校验：存在且唯一→替换；不存在→错误；多处→错误+行号
2. edit_executor 串行执行同波多个 replace（不并行，因后一个依赖前一个改完的草稿）
3. last_shot 波前快照：进 edit_executor 时存当前 resume_shot，供 approve_node 撤销整波
4. 失败处理：某 replace 失败时，已成功的不回滚，失败的那个返错误 ToolMessage

通过 = 行为符合预期。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, ToolMessage


# 模拟 ResumeState 子集 ------------------------------------------------------- #
class _State(dict):  # type: ignore[type-arg]
    """dict-based state stub（省去 TypedDict 样板）。"""


# grep_replace 核心逻辑 ------------------------------------------------------- #
def grep_replace_one(draft: str, grep_target: str, replace_content: str) -> tuple[str, str]:
    """单次 find→replace，唯一性校验。

    Returns:
        (new_draft, error_msg) —— 成功 error_msg=""，失败 new_draft=draft 不变。
    """
    if not grep_target:
        return draft, "grep_target 为空"
    count = draft.count(grep_target)
    if count == 0:
        return draft, f"未找到「{grep_target[:40]}...」"
    if count > 1:
        # 列出各处行号帮 LLM 重定位
        lines = draft.splitlines()
        hits = [
            f"L{i+1}: {ln.strip()[:50]}"
            for i, ln in enumerate(lines)
            if grep_target in ln
        ]
        return draft, f"「{grep_target[:30]}...」命中 {count} 处，需更长片段唯一定位: {hits}"
    return draft.replace(grep_target, replace_content), ""


# edit_executor：串行执行 + last_shot 波前快照 -------------------------------- #
def edit_executor(state: _State, ai_message: AIMessage) -> _State:
    """阶段2 edit_executor 原型：串行执行同波 grep_replace，波前打 last_shot。

    - 波前：把当前 resume_shot 存 last_shot（一次，不是每个 replace 前）
    - 串行：按 tool_calls 顺序逐个执行，后一个依赖前一个改完的草稿
    - 失败：已成功的不回滚，失败的返错误 ToolMessage（草稿保留已改部分）
    """
    draft = state.get("resume_shot", "")
    last_shot = draft  # 波前快照
    tcs = getattr(ai_message, "tool_calls", []) or []
    tool_messages: list[ToolMessage] = []
    for tc in tcs:
        args = tc.get("args", {}) or {}
        grep_target = str(args.get("grep_target", ""))
        replace_content = str(args.get("replace_content", ""))
        new_draft, err = grep_replace_one(draft, grep_target, replace_content)
        if err:
            tool_messages.append(
                ToolMessage(content=f"失败: {err}", tool_call_id=str(tc.get("id", "")))
            )
        else:
            draft = new_draft
            tool_messages.append(
                ToolMessage(
                    content=f"已替换「{grep_target[:30]}...」",
                    tool_call_id=str(tc.get("id", "")),
                )
            )
    return _State(
        resume_shot=draft,
        last_shot=last_shot,
        messages=tool_messages,
    )


# 撤销（approve_node reject/suggest 时） -------------------------------------- #
def rollback(state: _State) -> _State:
    """撤销整波：last_shot → resume_shot。"""
    return _State(resume_shot=state.get("last_shot", ""))


def main() -> None:
    draft = "## 教育经历\n学校A\n## 工作经历\n公司B\n## 项目经历\n项目C\n"

    # 1. 唯一命中 → 替换
    d, err = grep_replace_one(draft, "学校A", "学校A改")
    assert err == "" and d == draft.replace("学校A", "学校A改")
    print("[1] 唯一命中替换: OK")

    # 2. 不存在 → 错误，草稿不变
    d, err = grep_replace_one(draft, "不存在X", "Y")
    assert err != "" and d == draft
    print("[2] 不存在报错且不改: OK")

    # 3. 多处命中 → 错误+行号
    d, err = grep_replace_one(draft, "## ", "### ")
    assert "命中 3 处" in err and d == draft
    print("[3] 多处命中报错+行号: OK")

    # 4. edit_executor 串行执行 2 个成功 replace + last_shot 波前快照
    tc1 = {"id": "c1", "name": "grep_replace", "args": {"grep_target": "学校A", "replace_content": "大学X"}}
    tc2 = {"id": "c2", "name": "grep_replace", "args": {"grep_target": "公司B", "replace_content": "公司Y"}}
    ai = AIMessage(content="", tool_calls=[tc1, tc2])
    st = edit_executor(_State(resume_shot=draft), ai)
    assert "大学X" in st["resume_shot"] and "公司Y" in st["resume_shot"]
    assert st["last_shot"] == draft  # 波前快照=改前
    assert len(st["messages"]) == 2 and all(m.content.startswith("已替换") for m in st["messages"])
    print("[4] 串行执行2个成功+last_shot波前: OK")

    # 5. 同波中途失败：已成功不回滚，失败的返错误，草稿保留已改部分
    tc3 = {"id": "c3", "name": "grep_replace", "args": {"grep_target": "项目C", "replace_content": "项目Z"}}
    tc4 = {"id": "c4", "name": "grep_replace", "args": {"grep_target": "不存在Q", "replace_content": "Z"}}
    ai2 = AIMessage(content="", tool_calls=[tc3, tc4])
    st2 = edit_executor(_State(resume_shot=draft), ai2)
    assert "项目Z" in st2["resume_shot"]  # 第一个成功
    assert any("失败" in m.content for m in st2["messages"])  # 第二个失败
    assert st2["last_shot"] == draft
    print("[5] 中途失败已成功不回滚+失败返错: OK")

    # 6. rollback 撤销整波：last_shot → resume_shot
    st3 = rollback(st)  # st 是 case4 改完的
    assert st3["resume_shot"] == draft  # 回到改前
    print("[6] rollback撤销整波: OK")

    print("\nALL PASSED")


if __name__ == "__main__":
    main()
