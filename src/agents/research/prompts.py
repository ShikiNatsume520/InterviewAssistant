"""research_agent prompt 集中管理。

R3 重构：把 ``OUTLINE_PROMPT`` / ``DISTILL_PROMPT`` / ``FINALIZE_PROMPT``（原 graph.py）
迁入此模块，含变量的封装为 ``build_*()`` 函数。
"""

from __future__ import annotations

_OUTLINE_PROMPT_TEMPLATE: str = """你是深研检索规划师。给定知识缺口主题，生成 3-5 个**互补的**检索词，
用于在 DuckDuckGo 上多角度搜集资料补足该缺口。

规则:
1. 检索词为简短查询短语（可中可英，技术主题优先英文以匹配官方文档）。
2. 3-5 个，覆盖该主题的不同侧面（概念定义、实战用法、常见坑、最佳实践等）。
3. 只输出 JSON 数组，不要 markdown 代码块标记，不要解释。

=== 知识缺口主题 ===
{gap_topic}
{feedback_block}
输出示例:
["StateGraph state management", "LangGraph add_messages reducer", "LangGraph state schema TypedDict"]
"""


def build_outline_prompt(gap_topic: str, feedback_block: str) -> str:
    """构造深研大纲生成 prompt。

    Args:
        gap_topic: 知识缺口主题。
        feedback_block: 用户对上次大纲的建议块（为空则无）。

    Returns:
        填充后的大纲 prompt。
    """
    return _OUTLINE_PROMPT_TEMPLATE.format(
        gap_topic=gap_topic, feedback_block=feedback_block
    )


_DISTILL_PROMPT_TEMPLATE: str = """你是资料提炼师。从下面这篇网页正文里, 提炼与检索主题相关的**事实性知识**,
整理成结构化笔记（要点列表, 每条一句, 保留关键技术细节如类名/函数名/参数）。

只输出提炼后的笔记正文, 不要额外解释, 不要 markdown 代码块标记。

=== 检索词 ===
{query}

=== 网页标题 ===
{title}

=== 网页正文 ===
{content}
"""


def build_distill_prompt(query: str, title: str, content: str) -> str:
    """构造网页正文提炼 prompt（content 由调用方截断）。

    Args:
        query: 产生本笔记的检索词。
        title: 网页标题。
        content: 网页正文（已截断）。

    Returns:
        填充后的提炼 prompt。
    """
    return _DISTILL_PROMPT_TEMPLATE.format(query=query, title=title, content=content)


_FINALIZE_PROMPT_TEMPLATE: str = """你是知识整理师。把多轮深研搜集的笔记整合成一篇结构化 Markdown 文档,
用于补足本地知识库的「{gap_topic}」缺口。

要求:
1. 以一级标题 ``# 深研资料: {gap_topic}`` 开头。
2. 按主题分若干二级标题 ``## 子主题`` 组织内容。
3. 每条笔记整理成要点, 保留关键事实与来源链接。
4. 末尾用 ``## 来源`` 列出所有引用的 URL（每行一个）。
5. 只输出 Markdown 正文, 不要额外解释。

=== 深研笔记列表 ===
{notes}
"""


def build_finalize_prompt(gap_topic: str, notes: str) -> str:
    """构造深研笔记整合 prompt。

    Args:
        gap_topic: 知识缺口主题。
        notes: 渲染后的笔记文本（由 ``_render_notes`` 产出）。

    Returns:
        填充后的整合 prompt。
    """
    return _FINALIZE_PROMPT_TEMPLATE.format(gap_topic=gap_topic, notes=notes)
