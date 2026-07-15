"""resume_agent prompt 集中管理。

阶段3（常驻编辑会话改造）：随 schema 重写。``build_chat_prompt`` 是子图唯一
LLM 决策点 ``chat_node`` 的 system prompt——注入当前草稿、计划（若有）、选区
hint，并说明工具使用与「无 tool_call 即总结」规则。``build_plan_prompt`` 供
``plan_node`` 产出修改计划。
"""

from __future__ import annotations

_CHAT_PROMPT_TEMPLATE: str = """你是简历优化助手。当前正在常驻编辑会话中，根据用户请求对简历（markdown）做修改并请用户确认。

## 当前简历草稿
{resume_shot}

## 修改计划（若有，作为执行参考）
{plan_block}

## 用户选区（位置提示，仅供参考，不限于只改此处）
{selection_block}

## 可用工具
- grep_replace(grep_target, replace_content): 在草稿中定位 ``grep_target``（必须存在且唯一）并用 ``replace_content`` 替换。可在一波中调用多次（系统串行执行）。``grep_target`` 失败时会返回错误（不存在/多处命中），请据错误用更长上下文片段重发。
- request_plan(): 当修改较复杂、或没把握理解用户意图时调用，进入计划流程让用户协同制定详细计划。

## 决策准则
1. 简单修改：直接调用 ``grep_replace``，可一波发多个（系统串行执行）。
2. 复杂修改、或不确定用户意图时：调用 ``request_plan``，让用户审阅计划、协同定稿后再执行。
3. 修改全部完成后：**直接回复总结文本，不调用任何工具**——你的回复文本会作为本次会话总结保存，并展示给用户进入待命。
4. ``grep_target`` 要从「当前简历草稿」中**原样复制**足够长的片段以保证唯一命中；不要凭记忆编造。
"""


def _render_selection(selection: object) -> str:
    """把选区渲染为 prompt 文本块。"""
    if not selection or not isinstance(selection, dict):
        return "（无）"
    text = str(selection.get("text", ""))
    start = selection.get("start_line")
    end = selection.get("end_line")
    if not text and start is None:
        return "（无）"
    return f"行 {start}-{end}（选区原文：{text[:80]}{'...' if len(text) > 80 else ''}）"


def _render_plan(plan: list[str]) -> str:
    """把计划渲染为带序号的文本块。"""
    if not plan:
        return "（无计划，按用户当前请求自主决策）"
    return "\n".join(f"{i + 1}. {s}" for i, s in enumerate(plan))


def build_chat_prompt(resume_shot: str, plan: list[str], selection: object) -> str:
    """构造 chat_node 的 system prompt。

    Args:
        resume_shot: 当前简历草稿（markdown 字符串）。
        plan: 当前修改计划（步骤列表，可能为空）。
        selection: 当前选区 hint（Selection dict 或 None）。

    Returns:
        填充后的 chat_node system prompt。
    """
    return _CHAT_PROMPT_TEMPLATE.format(
        resume_shot=resume_shot,
        plan_block=_render_plan(plan),
        selection_block=_render_selection(selection),
    )


_PLAN_PROMPT_TEMPLATE: str = """你是简历优化规划师。根据用户简历与最新请求，制定**可执行的步骤清单**。

规则：
1. 每个步骤是一句具体的操作描述，对应后续可由 ``grep_replace`` 完成的修改。
2. 步骤数量 2-5 个，按逻辑顺序排列。
3. 只输出 JSON 数组（纯 JSON，不要 markdown 代码块标记，不要解释）。

=== 用户简历 ===
{resume}

=== 最新请求 ===
{intent}

输出格式示例：
["补充项目经历，突出 LangGraph 多智能体系统", "精简技能列表至 5 项", "调整章节顺序为 教育→项目→技能"]
"""


def build_plan_prompt(resume: str, intent: str) -> str:
    """构造 plan_node 的规划 prompt。

    Args:
        resume: 当前简历草稿。
        intent: 最新用户请求（用于驱动规划）。

    Returns:
        填充后的规划 prompt。
    """
    return _PLAN_PROMPT_TEMPLATE.format(resume=resume, intent=intent)
