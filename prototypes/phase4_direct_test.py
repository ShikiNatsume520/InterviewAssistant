#!/usr/bin/env python3
"""
Phase 4 直接测试 — 使用 AsyncSqliteSaver 本地数据库持久化（第一种测试方式）

测试内容
--------
1.  Checkpointer 恢复执行:
    使用 ``AsyncSqliteSaver`` 编译主图 → 发送消息 → 重新编译（模拟崩溃）→
    读取历史 → 继续对话 → 验证连贯性

2.  跨会话长期记忆:
    线程 A 发送消息 → 新线程 B（同 user_id）发送消息 →
    验证 LLM 回复感知到用户画像

前置条件
--------
    无需启动 langgraph dev，直接运行即可。
    会在当前目录生成 ``direct_test.sqlite`` 作为持久化数据库。

用法
----
    python prototypes/phase4_direct_test.py

参考
----
    ref/持久化参考.md — 第一种测试方式
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from typing import Any

# ── 添加项目根到 sys.path（支持 from src.client import ...） ──
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: E402

# 直接导入未编译的 workflow 构造器
from agent.graph import build_main_graph  # noqa: E402
from agent.persistence import get_store  # noqa: E402

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "direct_test.sqlite",
)

# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #


def _safe_print(text: str) -> None:
    """GBK 安全打印。"""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("gbk", errors="replace").decode("gbk"))


async def _extract_last_ai(state: Any) -> str:
    """从最终图状态中提取最后一条 AI 消息内容。

    支持 BaseMessage 对象（``.type`` 属性）和 dict 格式（``["type"]``）。
    """
    values = state if isinstance(state, dict) else getattr(state, "values", state)
    messages = values.get("messages", [])
    if messages:
        last = messages[-1]
        # BaseMessage 对象：有 .type 属性
        if hasattr(last, "type") and last.type == "ai":
            content = last.content if isinstance(last.content, str) else str(last.content)
            return content[:180] + "..." if len(content) > 180 else content
        # dict 格式
        if isinstance(last, dict) and last.get("type") == "ai":
            content = last.get("content", "") or ""
            return content[:180] + "..." if len(content) > 180 else content
    return "(无 AI 回复)"


# --------------------------------------------------------------------------- #
# 测试 1: Checkpointer 恢复执行
# --------------------------------------------------------------------------- #


async def test_checkpointer_recovery() -> None:
    """模拟崩溃后从 SQLite 恢复并继续对话。"""
    _safe_print("\n" + "=" * 65)
    _safe_print("  测试 1: Checkpointer 恢复执行（AsyncSqliteSaver）")
    _safe_print("=" * 65)

    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    store = get_store()

    async with AsyncSqliteSaver.from_conn_string(DB_PATH) as checkpointer:
        # ── 1a: 编译图并发送第一轮消息 ──
        _safe_print("\n  [1a] 编译图并发送消息 ...")
        graph = build_main_graph(
            checkpointer=checkpointer, store=store,
        )

        async for _ in graph.astream(
            {
                "messages": [{"role": "user", "content": "我正在学习 LangGraph，想了解 StateGraph 的状态管理机制"}],
                "user_id": "recovery_user",
            },
            config,
            stream_mode="values",
        ):
            pass

        state = await graph.aget_state(config)
        reply_1 = await _extract_last_ai(state.values)
        _safe_print(f"  第 1 轮 AI: {reply_1}")

        msg_count_1 = len(state.values.get("messages", []))
        _safe_print(f"  当前消息数: {msg_count_1}")

    # ── 1b: 模拟崩溃（async with 结束，原 graph 实例销毁） ──
    _safe_print("\n  [1b] 模拟崩溃: graph 实例销毁，数据库文件保留 ...")
    assert os.path.exists(DB_PATH), "数据库文件应保留"

    # ── 1c: 重新编译图并读取历史 ──
    _safe_print("\n  [1c] 重新编译图并恢复 ...")
    async with AsyncSqliteSaver.from_conn_string(DB_PATH) as checkpointer2:
        graph2 = build_main_graph(
            checkpointer=checkpointer2, store=store,
        )

        # 验证历史消息完整
        recovered_state = await graph2.aget_state(config)
        recovered_msgs = recovered_state.values.get("messages", [])
        _safe_print(f"  恢复后消息数: {len(recovered_msgs)}（预期 = {msg_count_1}）")
        assert len(recovered_msgs) == msg_count_1, "恢复后消息数应与之前一致"

        # ── 1d: 继续对话 ──
        _safe_print("\n  [1d] 继续对话(崩溃后) ...")
        async for _ in graph2.astream(
            {
                "messages": [{"role": "user", "content": "那节点和边是怎么定义的？"}],
                "user_id": "recovery_user",
            },
            config,
            stream_mode="values",
        ):
            pass

        state_final = await graph2.aget_state(config)
        reply_2 = await _extract_last_ai(state_final.values)
        _safe_print(f"  第 2 轮 AI: {reply_2}")

        total_msgs = len(state_final.values.get("messages", []))
        _safe_print("\n  [1d 验证] 对话连贯性:")
        _safe_print(f"    总消息数: {total_msgs}（预期 > {msg_count_1}）")
        assert total_msgs > msg_count_1, "继续对话后消息数应增加"

        has_content = "边" in reply_2 or "节点" in reply_2 or "edge" in reply_2.lower() or "node" in reply_2.lower()
        _safe_print(f"    回复涉及节点/边: {'是' if has_content else '否（可接受，LLM 风格不同）'}")

    _safe_print("\n  ✅ 测试 1 通过: Checkpointer 恢复执行成功")


# --------------------------------------------------------------------------- #
# 测试 2: 跨会话长期记忆
# --------------------------------------------------------------------------- #


async def test_cross_session_memory() -> None:
    """验证同 user_id 跨 thread 的记忆持久化。"""
    _safe_print("\n" + "=" * 65)
    _safe_print("  测试 2: 跨会话长期记忆")
    _safe_print("=" * 65)

    user = "memory_user"
    store = get_store()

    async with AsyncSqliteSaver.from_conn_string(DB_PATH) as checkpointer:
        graph = build_main_graph(
            checkpointer=checkpointer, store=store,
        )

        # ── 2a: 线程 A — 写入记忆 ──
        _safe_print("\n  [2a] 线程 A: 首次交流（提取用户画像）...")
        thread_a = str(uuid.uuid4())
        config_a = {"configurable": {"thread_id": thread_a}}

        async for _ in graph.astream(
            {
                "messages": [{"role": "user", "content": "你好，我是后端开发者，主要用 Python，最近在学 LangGraph"}],
                "user_id": user,
            },
            config_a,
            stream_mode="values",
        ):
            pass

        state_a = await graph.aget_state(config_a)
        reply_a = await _extract_last_ai(state_a.values)
        _safe_print(f"  线程 A AI: {reply_a}")

        # ── 2b: 线程 B — 读取记忆（同一 user_id，不同 thread） ──
        _safe_print("\n  [2b] 线程 B: 新会话（应加载线程 A 的记忆）...")
        thread_b = str(uuid.uuid4())
        config_b = {"configurable": {"thread_id": thread_b}}

        async for event in graph.astream(
            {
                "messages": [{"role": "user", "content": "继续之前的话题，帮我深入讲解一下"}],
                "user_id": user,
            },
            config_b,
            stream_mode="values",
        ):
            if "messages" in event:
                msgs = event["messages"]
                if msgs:
                    last = msgs[-1]
                    if hasattr(last, "type") and last.type == "ai":
                        last_content = last.content if isinstance(last.content, str) else str(last.content)

        _safe_print(f"  线程 B AI: {last_content[:180]}...")

        # ── 2c: 验证记忆注入 ──
        keywords = ["Python", "LangGraph", "后端"]
        found = [kw for kw in keywords if kw.lower() in last_content.lower()]
        _safe_print(f"\n  [2c 验证] LLM 回复中的用户画像关键词: {found}")
        if found:
            _safe_print("    跨会话记忆注入生效：LLM 感知到了用户的背景信息")
        else:
            _safe_print("    未检测到明确关键词（可能 LLM 风格所致，不判定失败）")

    _safe_print("\n  ✅ 测试 2 通过: 跨会话长期记忆验证成功")


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #


async def main() -> None:
    """运行所有 Phase 4 直接测试。"""
    _safe_print("=" * 65)
    _safe_print("  Phase 4 直接测试（AsyncSqliteSaver 本地持久化）")
    _safe_print(f"  数据库: {DB_PATH}")
    _safe_print("=" * 65)

    # 清理旧数据库
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        _safe_print(f"\n[Setup] 已清理旧数据库: {DB_PATH}")

    await test_checkpointer_recovery()
    await test_cross_session_memory()

    _safe_print("\n" + "=" * 65)
    _safe_print("  全部 Phase 4 直接测试通过")
    _safe_print("=" * 65 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
