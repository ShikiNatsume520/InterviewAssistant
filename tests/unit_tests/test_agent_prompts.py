"""注册表驱动的 Main Prompt 组合与缓存测试。"""

from agents.main.prompts import BASE_SYSTEM_PROMPT, build_system_prompt


def test_static_prompt_uses_only_registered_guidance_and_is_cached() -> None:
    build_system_prompt.cache_clear()
    snapshot = (
        ("rag_agent", "### RAG Agent\n使用本地知识库。"),
        ("resume_agent", "### Resume Agent\n负责专业编辑。"),
    )

    first = build_system_prompt(snapshot)
    second = build_system_prompt(snapshot)

    assert first is second
    assert BASE_SYSTEM_PROMPT in first
    assert "RAG Agent" in first
    assert "Resume Agent" in first
    assert "Research Agent" not in first
    assert build_system_prompt.cache_info().misses == 1
    assert build_system_prompt.cache_info().hits == 1


def test_changed_registry_snapshot_builds_a_new_prompt() -> None:
    build_system_prompt.cache_clear()
    first = build_system_prompt((("rag_agent", "RAG"),))
    changed = build_system_prompt(
        (("rag_agent", "RAG"), ("research_agent", "Research"))
    )

    assert first != changed
    assert "Research" not in first
    assert "Research" in changed
    assert build_system_prompt.cache_info().misses == 2
