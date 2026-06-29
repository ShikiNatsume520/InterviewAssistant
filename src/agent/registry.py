"""Agent 注册中心 — 管理子智能体元数据与工具列表。"""

from __future__ import annotations

from typing import Callable

from langchain_core.tools import BaseTool

from agent.state import MainState


class SubAgentMetaData:
    """子智能体注册元数据。

    Attributes:
        name: 注册名（同时也是工具名和 LangGraph 节点名）。
        description: 人类可读的职责描述。
        tool: LLM ``bind_tools`` 用的 ``BaseTool`` 对象。
        wrapper_node_fn: 包装节点函数。接收 ``MainState`` → 更新 ``dict``。
    """

    def __init__(
        self,
        name: str,
        tool: BaseTool,
        wrapper_node_fn: Callable[[MainState], dict],
    ) -> None:
        """初始化 ``SubAgentMetaData``。

        Args:
            name: 子智能体注册名。
            tool: LLM 绑定的工具对象（由 ``@tool`` 或 ``as_tool`` 生成）。
            wrapper_node_fn: 主图中的包装节点函数。
        """
        self.name = name
        self.tool = tool
        self.wrapper_node_fn = wrapper_node_fn


class AgentRegistry:
    """子智能体注册中心。

    职责：
    - 管理普通工具与子智能体工具的统一直出列表。
    - 提供路由判断能力（``is_sub_agent``）。
    """

    def __init__(self, basic_tools: list[BaseTool] | None = None) -> None:
        """初始化注册中心。

        Args:
            basic_tools: 普通工具列表（非子智能体），默认为空。
        """
        self._basic_tools: list[BaseTool] = basic_tools or []
        self._sub_agents: dict[str, SubAgentMetaData] = dict()

    @property
    def basic_tools(self) -> list[BaseTool]:
        """返回普通工具列表的副本。"""
        return list(self._basic_tools)

    @property
    def registered(self) -> dict[str, SubAgentMetaData]:
        """返回所有已注册子智能体的元数据快照。"""
        return dict(self._sub_agents)

    def register(self, meta: SubAgentMetaData) -> None:
        """注册一个子智能体。

        Args:
            meta: 子智能体元数据。
        """
        self._sub_agents[meta.name] = meta

    def get_all_tools(self) -> list[BaseTool]:
        """返回普通工具 + 所有子图工具 的平铺列表，供 LLM ``bind_tools``。

        Returns:
            合并后的工具列表。
        """
        tools = list(self._basic_tools)
        for meta in self._sub_agents.values():
            tools.append(meta.tool)
        return tools

    def is_sub_agent(self, tool_name: str) -> bool:
        """判断工具名是否对应一个已注册的子智能体。

        Args:
            tool_name: 工具名。

        Returns:
            是否为子智能体。
        """
        return tool_name in self._sub_agents

    def get_sub_agent_names(self) -> set[str]:
        """返回所有已注册子智能体的名字集合。"""
        return set(self._sub_agents.keys())
