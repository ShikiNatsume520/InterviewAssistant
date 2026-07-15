"""phase13: resume 子图阶段4真 LLM 端到端（select_resume + persist 闭环）。

验证阶段4：wrapper 纯透传只传 intent → select_resume interrupt 选简历 → 真 LLM
chat_node 编辑 → approve → finish → hitl exit(save=True) → persist 写源文件。

依赖真 API，不进 CI，放 prototypes 手动验证。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.resume.graph import build_resume_workflow
from kernel.paths import RESUMES_DIR


def _safe(s: str, n: int = 100) -> str:
    return s[:n].encode("ascii", "replace").decode()


def main() -> None:
    graph = build_resume_workflow().compile(checkpointer=MemorySaver())
    tid = "phase13"
    config: dict[str, Any] = {"configurable": {"thread_id": tid}}
    sample_path = RESUMES_DIR / "sample.md"
    original = sample_path.read_text(encoding="utf-8")

    try:
        # 1. invoke 只传 intent（模拟 wrapper 纯透传）
        print("=== 1. invoke（select_resume interrupt） ===")
        graph.invoke(
            {"messages": [HumanMessage(content="把姓名从「八爪猫」改为「张三」")]},
            config,
        )
        st = graph.get_state(config)
        print(f"  next={st.next}")
        assert st.next == ("select_resume",), st.next

        # 2. 选 sample.md → 真 LLM chat_node → approve interrupt
        print("=== 2. resume 选 sample.md → chat_node → approve ===")
        graph.invoke(Command(resume={"resume_file": "sample.md"}), config)
        st = graph.get_state(config)
        vals = dict(st.values)
        print(f"  next={st.next}")
        print(f"  resume_shot 含张三: {'张三' in vals.get('resume_shot', '')}")
        print(f"  preview: {_safe(vals.get('resume_shot', ''))}")
        assert st.next == ("approve_node",), st.next
        assert "张三" in vals.get("resume_shot", ""), "真 LLM 应已改姓名"

        # 3. approve → chat_node(finish) → hitl interrupt
        print("=== 3. resume approve → finish → hitl ===")
        graph.invoke(Command(resume={"decision": "approve"}), config)
        st = graph.get_state(config)
        print(f"  next={st.next}")
        assert st.next == ("hitl_standby",), st.next

        # 4. exit save=True → persist 写 sample.md → END
        print("=== 4. resume exit(save=True) → persist → END ===")
        graph.invoke(Command(resume={"action": "exit", "save": True}), config)
        st = graph.get_state(config)
        print(f"  next={st.next}")
        assert st.next == (), st.next
        written = sample_path.read_text(encoding="utf-8")
        print(f"  sample.md 含张三: {'张三' in written}")
        assert "张三" in written, "persist 应覆盖 sample.md"
        assert "八爪猫" not in written

        print("\nALL PASSED — 阶段4真 LLM 端到端（select_resume+persist）通过")
    finally:
        sample_path.write_text(original, encoding="utf-8")
        print("  sample.md 已还原")


if __name__ == "__main__":
    main()
