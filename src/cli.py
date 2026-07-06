"""命令行入口。

提供 Index Agent 主动唤醒命令（前端未实现前手动激活处理一批 markdown 文件）。

用法::

    python -m cli index <file1.md> <file2.md>

Index Agent 是后台 agent（不进主图流程），详见
[CLAUDE.md](../CLAUDE.md)「Index Agent 定位」。
"""

from __future__ import annotations

import asyncio
import sys


async def _run_index_agent(target_files: list[str]) -> None:
    """唤醒 Index Agent 处理一批 markdown 文件（切片 + 灌库 + 维护 index.md）。"""
    from agents.index.graph import graph as index_graph

    result = await index_graph.ainvoke({"target_files": target_files})
    n_chunks = len(result.get("chunks", []))
    print(f"Index Agent 完成：{n_chunks} chunks 入库，索引已更新。")


def main() -> None:
    """CLI 入口。"""
    if len(sys.argv) < 3 or sys.argv[1] != "index":
        print("用法: python -m cli index <file1.md> [file2.md ...]")
        sys.exit(1)
    target_files = sys.argv[2:]
    asyncio.run(_run_index_agent(target_files))


if __name__ == "__main__":
    main()
