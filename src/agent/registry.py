"""主图子智能体注册表（显式清单布线）。

R1 重构：把"新增子 agent 需改 ALL_TOOLS / route_after_chat / path_map / add_node
四处"降为"一 wrapper 模块 + 清单一行"。

铁律：``REGISTRY`` 只作 build-time 布线清单，``node`` 是静态 wrapper 函数引用（非
工厂产物），``REGISTRY`` 绝不出现在节点函数体内——否则 Subscript 不可被
``find_subgraph_pregel`` 的 AST 闭包分析发现，Studio 将展不开子图内部节点
（验证见 ``prototypes/phase7_registry_studio_probe.py``）。
"""

from __future__ import annotations

from typing import Any, TypedDict

# 模块顶层 import 各 wrapper（触发各子图模块级编译，供 Studio 静态发现）。
# 依赖方向：registry → agent.tools.* → <agent>.graph（单向，无环——R0 已拆循环依赖）。
from agent.tools.rag_agent import rag_agent, rag_agent_node
from agent.tools.research_agent import research_agent, research_agent_node
from agent.tools.resume_agent import resume_agent, resume_agent_node


class SubAgentMeta(TypedDict):
    """子智能体布线元数据。

    Attributes:
        name: 节点名 + path_map key。
        tool: ``@tool`` 对象，供 ``ALL_TOOLS`` / ``bind_tools``。
        node: 静态 wrapper 函数引用（关键：是引用，不是生成器产物）。
        route_key: ``route_after_chat`` 返回值。
    """

    name: str
    tool: Any
    node: Any
    route_key: str


REGISTRY: list[SubAgentMeta] = [
    {
        "name": "rag_agent",
        "tool": rag_agent,
        "node": rag_agent_node,
        "route_key": "rag_agent",
    },
    {
        "name": "resume_agent",
        "tool": resume_agent,
        "node": resume_agent_node,
        "route_key": "resume_agent",
    },
    {
        "name": "research_agent",
        "tool": research_agent,
        "node": research_agent_node,
        "route_key": "research_agent",
    },
]
"""已登记子智能体清单（新增子 agent 在此追加一行 + 写一个 wrapper 模块）。"""

ROUTE_TABLE: dict[str, str] = {m["tool"].name: m["route_key"] for m in REGISTRY}
"""``route_after_chat`` 查表用：``{tool 名: route_key}``。"""
