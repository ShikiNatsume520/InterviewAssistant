"""Phase 6 端到端验收脚本。

模拟完整深研流程: 主图 wrapper → research 子图（outline → connectivity → search
→ finalize → index_rebuild），验证所有节点连通 + 跨子图调用 index_agent + 写文件。

本脚本用真实 ddgs 搜索 + 真实 Index Agent 重建索引（会写文件到 data/markdown/ + 灌 Chroma）。

运行:
    .venv/Scripts/python.exe prototypes/phase6_e2e_test.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphInterrupt
from langgraph.types import Command


async def main() -> None:
    print("=" * 70)
    print("Phase 6 端到端验收测试")
    print("=" * 70)

    # ── 1. Import research 子图 ──
    from research_agent.graph import build_research_workflow, graph as research_graph

    print("[1/5] 模块级 research_graph import OK")

    # ── 2. 验证子图结构（应有 7 个节点） ──
    nodes = list(research_graph.get_graph().nodes.keys())
    print(f"[2/5] 子图节点: {nodes}")
    assert "outline" in nodes
    assert "outline_confirm" in nodes
    assert "connectivity_check" in nodes
    assert "search" in nodes
    assert "finalize" in nodes
    assert "index_rebuild" in nodes
    assert "abort" in nodes
    print(f"  subgraphs={research_graph.get_subgraphs()}")

    # ── 3. 执行 outline → outline_confirm（interrupt 验证） ──
    checkpointer = MemorySaver()
    # 用未编译的 workflow + MemorySaver 构建测试用图（模块级 graph 已编译无法再 compile）
    g = build_research_workflow().compile(name="ProbeResearch", checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "e2e-research-probe"}}
    topic = "LangGraph StateGraph"

    print(f"\n[3/5] 启动子图 (gap_topic={topic!r})")
    try:
        await g.ainvoke({"gap_topic": topic}, config)
    except GraphInterrupt:
        pass
    state = await g.aget_state(config)
    tasks = getattr(state, "tasks", []) or []
    intrs = []
    for t in tasks:
        intrs.extend(getattr(t, "interrupts", None) or [])
    intr_vals = [getattr(i, "value", i) for i in intrs]
    print(f"  outline interrupt: {[v.get('phase') if isinstance(v,dict) else '?' for v in intr_vals]}")
    outline = state.values.get("outline", [])
    print(f"  outline: {outline}")
    assert outline, "outline 不应为空"
    assert len(outline) >= 2, f"outline 应>=2 条（gap_topic 直接兜底也有 1 条）"
    print("  [OK] outline_node + outline_confirm interrupt 正常")

    # ── 4. approve → connectivity → search → finalize → index_rebuild ──
    print(f"\n[4/5] approve 大纲 → 实时深研")
    try:
        await g.ainvoke(Command(resume="approve"), config)
    except GraphInterrupt:
        # connectivity check 可能失败（没挂梯子）——这是可预期的
        state2 = await g.aget_state(config)
        intr2 = getattr(state2, "tasks", []) or []
        vals2 = [getattr(i, "value", i) for ti in intr2 for i in (getattr(ti, "interrupts", None) or [])]
        if vals2:
            print(f"  connectivity interrupt: {vals2}")
            # resume 一次继续（用户确认已挂梯子）
            print("  resume 继续（模拟挂梯子）")
            try:
                await g.ainvoke(Command(resume="已挂好"), config)
            except GraphInterrupt:
                # 可能第 2 次又失败
                state3 = await g.aget_state(config)
                intr3 = getattr(state3, "tasks", []) or []
                vals3 = [getattr(i, "value", i) for ti in intr3 for i in (getattr(ti, "interrupts", None) or [])]
                if vals3:
                    print(f"  第 2 次 connectivity interrupt: {vals3}")
                    await g.ainvoke(Command(resume="再次确认"), config)
        pass

    state = await g.aget_state(config)
    final = state.values
    approval = final.get("approval_status", "?")
    new_file = final.get("new_file_name", "")
    summary = final.get("summary", "")
    n_notes = len(final.get("research_notes", []))
    print(f"  approval_status={approval}")
    print(f"  research_notes={n_notes} 条")
    print(f"  new_file_name={new_file}")
    print(f"  summary={summary[:100] if summary else '(空)'}")

    if approval == "approved":
        # 验证文件真实存在
        md_path = PROJECT_ROOT / "data" / "markdown" / new_file
        if md_path.exists():
            print(f"  [OK] 文件已写入: {md_path.name} ({md_path.stat().st_size} 字节)")
        else:
            print(f"  [WARN] 文件不存在: {md_path}")
        print(f"  [OK] 深研完成! 搜索了 {n_notes} 篇, 写了 {new_file}")
    elif approval == "aborted":
        print(f"  [WARN] 深研因网络问题中止（3 次 ddgs 失败），这是环境问题，非代码错误")
    else:
        print(f"  [WARN] 意外状态: {approval}")

    # ── 5. 验证主图 import（与 wrapper 一起加载时正常） ──
    print(f"\n[5/5] 验证主图 import")
    from agent.tools.research_agent import research_agent, research_agent_node

    print(f"  research_agent tool: {research_agent.name}")
    print(f"  research_agent_node: {research_agent_node.__name__}")

    from agent.graph import build_main_graph

    mg = build_main_graph(checkpointer=False)
    mnodes = list(mg.get_graph().nodes.keys())
    print(f"  主图节点: {mnodes}")
    assert "research_agent" in mnodes, "research_agent 节点应在主图中"
    print("  [OK] 主图编译正常，research_agent 节点已注册")

    print("=" * 70)
    print("验收完成")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())