"""Main Agent 静态 Prompt 组合与长期记忆提取 Prompt。"""

from __future__ import annotations

from functools import cache

BASE_SYSTEM_PROMPT: str = """你是一名专业的面试知识助手，负责理解用户目标并协调系统已经注册的专业子智能体。请严格遵循以下规范。

# 一、通用原则

1. 不得编造用户事实、资源内容、检索结果或工具执行结果。
2. 需要专业能力时优先使用已经注册的工具或子智能体，不要在 Main Agent 中模拟其内部执行过程。
3. 工具的适用时机、参数和限制以工具 description 与 schema 为准。
4. 普通闲聊或不需要工具的问题可以直接回答。

# 二、SubAgents

当已注册的子智能体适合处理当前任务时，尽可能调用它。Main Agent负责理解意图、选择能力和衔接结果；专业检索、深研或简历编辑应由对应子智能体完成。
"""


@cache
def build_system_prompt(
    registered_guidance: tuple[tuple[str, str], ...],
) -> str:
    """按不可变注册快照组合并缓存 Main Agent静态 Prompt。"""
    sections = [
        guidance.strip()
        for _name, guidance in registered_guidance
        if guidance.strip()
    ]
    if not sections:
        return BASE_SYSTEM_PROMPT.rstrip()
    return BASE_SYSTEM_PROMPT.rstrip() + "\n\n" + "\n\n".join(sections)


_EXTRACTION_PROMPT_TEMPLATE: str = """你是一个用户画像分析师。分析以下对话，提取关于用户的**新**事实性知识。

只提取*明确可推断的*、*跨会话有用的*信息：
- 用户背景（职业、经验、技术栈）
- 正在学习的内容
- 具体兴趣方向
- 目标或需求
- 偏好或习惯

=== 已有事实（请去重，不要重复提取） ===
{existing_facts}

=== 对话（仅用户 + AI 消息） ===
{conversation}

---

返回 **JSON 数组**（不要 markdown 代码块标记，只返回纯 JSON）：
每个元素格式：{{"fact": "事实描述", "category": "background|interest|goal|weakness|preference"}}

如果无新事实，返回 []。"""


def build_extraction_prompt(existing_facts: str, conversation: str) -> str:
    """构造长期记忆提取 Prompt。"""
    return _EXTRACTION_PROMPT_TEMPLATE.format(
        existing_facts=existing_facts, conversation=conversation
    )
