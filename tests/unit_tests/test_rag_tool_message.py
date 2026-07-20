"""RAG ToolMessage 的动态消费约束测试。"""

from agents.main.tools.rag_agent import build_rag_tool_content
from agents.rag.state import Citation


def _citation() -> Citation:
    return {
        "file_path": "知识库/Agent Notes.md",
        "start_line": 12,
        "end_line": 18,
        "content": "RAG 会先检索相关资料。",
        "score": 0.91,
    }


def test_candidates_include_strict_reference_contract() -> None:
    content = build_rag_tool_content("什么是 RAG？", [_citation()], None)

    assert "状态：ok" in content
    assert "[1] 知识库/Agent Notes.md L12-18" in content
    assert "以下资料仅为候选，不要求全部使用" in content
    assert "## 参考资料" in content
    assert "只列实际使用的资料" in content
    assert "必须从候选资料原样复制" in content
    assert "必须是回答最后一个区块" in content


def test_gap_requests_consent_before_research_without_references() -> None:
    content = build_rag_tool_content("冷门主题", [], "冷门主题")

    assert "状态：knowledge_gap" in content
    assert "知识缺口：冷门主题" in content
    assert "询问用户是否允许使用 Research Agent" in content
    assert "获得用户明确同意前，不得自行启动" in content
    assert "不得输出 `## 参考资料` 区块" in content


def test_empty_results_preserve_query_as_gap_context() -> None:
    content = build_rag_tool_content("待检索问题", [], None)

    assert "状态：no_results" in content
    assert "检索主题：待检索问题" in content
    assert "询问用户是否允许使用 Research Agent" in content
