# 注册新 Agent 指导手册

本手册说明如何向主图注册一个新的子智能体（sub-agent）。**目标**：新增一个子 agent 只需「写一个 wrapper 模块 + 在 `REGISTRY` 追加一行」，不碰 `graph.py` / `routing.py` 的布线逻辑。

> 适用版本：R1 重构后（registry 显式清单布线）。架构背景见 [CLAUDE.md](../CLAUDE.md)「当前架构」与 [openspec/changes/refactor-agent-v1/design.md](../openspec/changes/refactor-agent-v1/design.md) D2。

---

## 架构速览

主图（`src/agent/graph.py`）通过 `agent.registry` 的 `REGISTRY` 显式清单布线子智能体。每个子智能体由两部分组成：

| 部分 | 位置 | 职责 |
|---|---|---|
| 子图 | `src/<agent>/`（`graph.py` / `state.py` / `tools/`） | 自包含的 LangGraph 子图，模块级编译 `graph` 实例 |
| 主图 wrapper | `src/agent/tools/<agent>.py` | `@tool` 工具 + 静态 wrapper 节点，顶层 import 子图并 `ainvoke` |

`REGISTRY` 是一张布线清单，`ALL_TOOLS` / `ROUTE_TABLE` / `path_map` / `add_node` / `add_edge` 全部由它派生。

---

## 注册步骤

### 1. 写子图（若尚不存在）

在 `src/<agent>/` 建包，自包含其逻辑：

- `state.py`：`<Agent>State(TypedDict)` 子图状态 schema
- `graph.py`：节点函数 + `build_<agent>_workflow()` + **模块级** `graph = build_<agent>_workflow().compile(name="<agent>")`
- `tools/`（按需）

**约束**：子图只 import `kernel.*` + 自身模块，**不 import `agent.*`**（避免循环依赖——R0 已用此规则拆环）。

### 2. 写主图 wrapper

在 `src/agent/tools/<agent>.py`：

```python
"""<agent> 子智能体：Tool 定义 + 包装节点函数。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.errors import GraphInterrupt

from agent.state import MainState
from kernel.logging import dlog, slog
from <agent>.graph import graph as <agent>_graph  # ⚠️ 模块顶层 import（硬约束，见下）


@tool
def <agent>(...) -> str:
    """工具描述（LLM 据此决定何时调用）。"""
    raise RuntimeError(
        "<agent> tool 不应被 ToolNode 执行——应由 route_after_chat 拦截"
    )


async def <agent>_node(state: MainState, config: RunnableConfig) -> dict[str, Any]:
    """异步 wrapper：从主图 tool_call 提取参数 → ainvoke 子图 → 回填 ToolMessage。"""
    # 1. 从 state.messages[-1].tool_calls 提取参数 + 真实 tool_call_id
    # 2. await <agent>_graph.ainvoke({...}, config)   ← 裸名引用（硬约束）
    # 3. 把 result 包成 ToolMessage(content=..., tool_call_id=...) 回填
    # 4. 子图 interrupt 时 ainvoke 抛 GraphInterrupt——透传（不 catch），主图挂起
    ...
```

**关键**：wrapper 的状态翻译（MainState ↔ 子图 state）、`tool_call_id` 改写、`GraphInterrupt` 透传是 per-agent 的，不能完全 generic 化。参考现有三个 wrapper 的写法（见下「完整示例」）。

### 3. 在 `REGISTRY` 追加一行

`src/agent/registry.py`：

```python
# 顶层 import 区追加：
from agent.tools.<agent> import <agent>, <agent>_node

# REGISTRY 列表追加：
REGISTRY: list[SubAgentMeta] = [
    ...,
    {
        "name": "<agent>",
        "tool": <agent>,
        "node": <agent>_node,
        "route_key": "<agent>",
    },
]
```

**完成。** `ALL_TOOLS`（`bind_tools`）、`ROUTE_TABLE`（`route_after_chat` 查表）、`path_map`、`add_node`、`add_edge` 全部由 `REGISTRY` 自动派生，**无需改 `graph.py` / `routing.py`**。

### 4.（可选）langgraph.json 注册子图独立入口

若想在 Studio 单独调试子图（不通过主图），在 `langgraph.json` 的 `graphs` 加：

```json
"<agent>": "./src/<agent>/graph.py:graph"
```

主图流程不依赖此项，仅调试用。

---

## ⚠️ Studio 子图发现硬约束（不可违反）

LangGraph Studio 展开子图内部节点依赖编译时 `find_subgraph_pregel`（`langgraph/pregel/_utils.py`）填充 `PregelNode.subgraphs`。机制：对节点函数做 AST 分析找「load 未 store」的名字，再用 `inspect.getclosurevars` 取 `{globals, nonlocals}` 匹配到 Pregel 实例。

### 必须

wrapper **模块顶层** `from <agent>.graph import graph as X` + 函数体**裸名** `await X.ainvoke(...)`。

`X` 是模块全局，AST 解析为 Pregel → `subgraphs` 被填充 → Studio 可展开。

### 禁止（均导致 `subgraphs` 为空，Studio 展不开）

| 写法 | 原因 |
|---|---|
| ❌ 惰性 getter（`_get_graph()` 返回值是函数非 Pregel） | 解析到的是函数，非 Pregel |
| ❌ 函数体内 `from ... import as rg` | `rg` 是 local（Store+Load），被排除 |
| ❌ 函数体内编译 `build_x().compile()` | 结果是 local |
| ❌ `REGISTRY[i].subgraph.ainvoke()` | Subscript 不可被 AST 解析 |
| ❌ 工厂合成闭包（除非闭包直接捕获 Pregel 为 nonlocal） | 不推荐，难调试 |

> **历史教训**：`research_agent` 曾用惰性 `_get_research_graph()`，Studio 一直展不开其子图（R0 前的潜伏 bug）。R1 改顶层 import 后修复。验证脚本：`prototypes/phase7_registry_studio_probe.py`（6/6 PASS）。

### 验证

```bash
python -c "import agent.graph as g; print([n for n in ['<agent>'] if g.graph.nodes[n].subgraphs])"
# 输出非空 = Studio 可展开
```

---

## 完整示例（参考现有三个 wrapper）

| wrapper | 特点 | 文件 |
|---|---|---|
| `rag_agent` | 无 interrupt，简单参数提取 + ToolMessage 回填 | [src/agent/tools/rag_agent.py](../src/agent/tools/rag_agent.py) |
| `resume_agent` | 带 `interrupt` 透传 + `ToolMessage` 的 `tool_call_id` 改写 | [src/agent/tools/resume_agent.py](../src/agent/tools/resume_agent.py) |
| `research_agent` | 带 `interrupt` + `SystemMessage` 反馈 + 跨子图 ainvoke index_agent | [src/agent/tools/research_agent.py](../src/agent/tools/research_agent.py) |

新 agent 按 `interrupt` / 反馈需求选最接近的模板复用。

---

## 检查清单

提交前确认：

- [ ] 子图 `graph.py` 模块级 `graph = ...compile(name="<agent>")`
- [ ] 子图不 import `agent.*`（只 `kernel.*` + 自身）
- [ ] wrapper 模块顶层 `from <agent>.graph import graph as X`
- [ ] wrapper 函数体裸名 `await X.ainvoke(...)`（非 getter / 非体内 import / 非工厂）
- [ ] `registry.py`：顶层 import wrapper + `REGISTRY` 追加一行
- [ ] `mypy --strict src/` + `ruff check src/` 通过
- [ ] `compiled.nodes["<agent>"].subgraphs` 非空
- [ ] `langgraph dev` 起图 + Studio 展开新子图 + `/v1/chat` 端到端激活新 agent
