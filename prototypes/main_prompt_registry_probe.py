"""验证 Main Prompt 按已注册子智能体组合，并仅缓存静态部分。"""

from __future__ import annotations

from functools import cache
from typing import TypedDict


class RegisteredAgent(TypedDict):
    name: str
    main_guidance: str


BASE_PROMPT = """你是 Main Agent，负责理解用户目标并协调已注册能力。

## SubAgents
当已注册的子智能体适合处理当前任务时，尽可能调用它，不要在 Main Agent 中模拟其专业执行过程。
"""

BUILD_CALLS = 0


@cache
def compose_static_prompt(registry: tuple[tuple[str, str], ...]) -> str:
    """只接收不可变注册快照，适合进程级缓存。"""
    global BUILD_CALLS
    BUILD_CALLS += 1
    sections = [guidance.strip() for _name, guidance in registry if guidance.strip()]
    return BASE_PROMPT.rstrip() + "\n\n" + "\n\n".join(sections)


def registry_snapshot(items: list[RegisteredAgent]) -> tuple[tuple[str, str], ...]:
    return tuple((item["name"], item["main_guidance"]) for item in items)


def prompt_for_turn(static_prompt: str, memory: str) -> str:
    """Memory 仍按用户和轮次动态追加，绝不进入静态缓存。"""
    return static_prompt + (f"\n\n## Memory\n{memory}" if memory else "")


def main() -> None:
    registered: list[RegisteredAgent] = [
        {
            "name": "rag_agent",
            "main_guidance": "### RAG Agent\n需要本地知识依据时优先委托检索。",
        },
        {
            "name": "resume_agent",
            "main_guidance": "### Resume Agent\nMain 负责确定资源，Resume 负责专业编辑。",
        },
    ]
    snapshot = registry_snapshot(registered)
    first = compose_static_prompt(snapshot)
    second = compose_static_prompt(snapshot)

    assert first == second
    assert BUILD_CALLS == 1, "相同注册快照应只组合一次"
    assert "RAG Agent" in first and "Resume Agent" in first
    assert "Research Agent" not in first, "未注册子智能体不得进入 Prompt"
    assert "search_type" not in first, "工具参数细节应留在 tool schema"

    alice = prompt_for_turn(first, "用户偏好中文简历")
    bob = prompt_for_turn(first, "用户偏好英文简历")
    assert alice != bob
    assert "中文简历" not in first and "英文简历" not in first
    assert BUILD_CALLS == 1, "动态 Memory 不应触发静态 Prompt 重算"

    changed = registered + [
        {
            "name": "research_agent",
            "main_guidance": "### Research Agent\n仅在知识缺口和用户同意后联网深研。",
        }
    ]
    third = compose_static_prompt(registry_snapshot(changed))
    assert "Research Agent" in third
    assert BUILD_CALLS == 2, "注册快照改变时应得到新的缓存项"
    print("PASS: Prompt 仅包含实际注册子智能体，静态部分缓存，Memory 保持动态。")


if __name__ == "__main__":
    main()
