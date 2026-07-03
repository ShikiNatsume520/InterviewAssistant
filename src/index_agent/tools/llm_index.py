"""LLM 索引维护工具。

调用 DeepSeek OpenAI 兼容端点，让 LLM 看「现有索引 + 新文件 chunks」后判断
合并/新建，输出更新后的完整索引表。

关键技术决策：DeepSeek thinking 模型不能强制 tool_choice（with_structured_output
会塞 tool_choice=required 触发 400）。改用 bind_tools 提供工具 + 默认 auto 让
LLM 自行调用；后端取 tool_calls 校验，未调用工具时兜底从文本抽 JSON。
"""

from __future__ import annotations

import json
from typing import Any

from dotenv import load_dotenv
from langchain_core.runnables import Runnable

from index_agent.state import Chunk, IndexRow, IndexUpdate
from src.client import get_chat_model

load_dotenv()

INDEX_PROMPT = """你是知识库索引维护 Agent。任务：根据【新入库文件内容】更新【现有索引】，输出更新后的完整索引表。

维护规则：
1. 对新文件的每个语义主题，判断现有索引中是否存在相似或可合并的项：
   - 若有相似项：合并——完善其摘要，并把该新文件名加入该项的 files（去重，保留已有文件）。
   - 若无相似项：新建一条索引行。
2. 与新文件无关的现有索引行，原样保留。
3. 一条索引可指向多个文件。
4. 摘要精炼，不超过 100 字符。
5. files 只写文件名（不含路径），如 langgraph_state.md。
6. 你必须调用 `IndexUpdate` 工具来输出结果，不要直接返回文本。

【现有索引】
{existing_index}

【新入库文件片段】
{new_chunks}

请调用 `IndexUpdate` 工具输出更新后的完整索引表。"""
"""索引维护 prompt 模板（含现有索引与新文件片段占位符）。"""


def _format_existing(rows: list[IndexRow]) -> str:
    """把现有索引行渲染成给 LLM 看的文本（编号列表）。

    Args:
        rows: 现有 IndexRow 列表。

    Returns:
        渲染后的文本；空列表时返回占位说明。
    """
    if not rows:
        return "（空，首次构建）"
    lines = []
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. 关键词: {r.keywords} | 摘要: {r.summary} | 文件: {r.files}")
    return "\n".join(lines)


def _format_chunks(chunks: list[Chunk]) -> str:
    """把新文件 chunks 渲染成给 LLM 看的文本（带 heading + 行号 + 内容）。

    Args:
        chunks: Chunk 列表（含 file_path/heading/start_line/end_line/content）。

    Returns:
        渲染后的文本块。
    """
    parts = []
    for c in chunks:
        parts.append(
            f"--- 文件: {c['file_path']} | 小节: {c['heading']} "
            f"(L{c['start_line']}-L{c['end_line']}) ---\n{c['content']}"
        )
    return "\n\n".join(parts)


def build_llm() -> Runnable[Any, Any]:
    """构造绑定 IndexUpdate 工具的 LLM（auto tool_choice）。

    LLM 端点/key/model 从 .env 的 DEEPSEEK_API_URL/DEEPSEEK_API_KEY/DEEPSEEK_MODEL 读。

    Returns:
        bind_tools 后的 Runnable（不强制 tool_choice）。
    """
    return get_chat_model("deepseek-v4-flash", [IndexUpdate])


async def update_index_via_llm(
    llm: Runnable[Any, Any],
    existing_rows: list[IndexRow],
    new_chunks: list[Chunk],
) -> IndexUpdate:
    """调用 LLM 维护索引，返回更新后的完整索引表。

    优先取 LLM 的 tool_calls 并经 pydantic 校验；若 LLM 未调用工具，则兜底从
    返回文本中抽取 JSON 解析。

    Args:
        llm: build_llm 产出的绑定工具的 Runnable。
        existing_rows: 现有索引行。
        new_chunks: 本批新文件的 Chunk 列表。

    Returns:
        LLM 输出并校验后的 IndexUpdate。

    Raises:
        RuntimeError: LLM 既未调用工具，返回文本中也无合法 JSON。
    """
    prompt = INDEX_PROMPT.format(
        existing_index=_format_existing(existing_rows),
        new_chunks=_format_chunks(new_chunks),
    )
    msg = await llm.ainvoke(prompt)
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        args: dict[str, Any] = tool_calls[0]["args"]
        return IndexUpdate.model_validate(args)
    # 兜底：未调用工具，尝试从文本抽 JSON
    content = getattr(msg, "content", "")
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise RuntimeError(f"LLM 未调用工具且无 JSON 文本: {text[:120]}")
    return IndexUpdate.model_validate(json.loads(text[start : end + 1]))

