"""Phase 7 原型: 验证 "显式 registry 列表 + 静态 wrapper" 能否被 Studio 子图发现。

背景
----
重构计划用 registry 显式列表布线主图 (新增子 agent 只改清单 + 一个 wrapper 模块)。
但 LangGraph Studio 展开子图内部节点依赖 ``PregelNode.subgraphs``, 它在编译时由
``find_subgraph_pregel(self.bound)`` 填充 (``langgraph/pregel/_utils.py``)。
本原型实证 5 种 wrapper 写法的 subgraphs 填充情况, 并最终验证 registry 列表布线
+ 静态 wrapper 的组合是否被 Studio 发现。

机制回顾 (读 langgraph 1.2.6 源码得知)
  find_subgraph_pregel → get_function_nonlocals(func):
    1. inspect.getsource(func) + ast.parse → FunctionNonLocals 找 "load 但未 store" 的名字;
    2. inspect.getclosurevars(func) → {globals, nonlocals};
    3. 名字匹配到值且值是 Pregel (compiled graph) → 命中; 支持 dotted (meta.subgraph 走 getattr 链);
    4. 不支持 Subscript (REGISTRY[i].x)、不支持 getter 函数返回值、不支持体内 import 的 local。

预期
  H1 模块顶层 import + 静态 wrapper        → 命中 (金标准, 重构采用)
  H2 惰性 getter (_get_graph())            → 空   (现有 research_agent 模式, 可能是潜伏 bug)
  H3 闭包工厂捕获子图为 nonlocal           → 命中 (探边界; 若通过则 Pattern 2 亦可作 fallback)
  H4 函数体内 inline import                → 空   (anti-pattern, 不会用)
  H5 函数体内就地编译                       → 空   (anti-pattern, 不会用)
  REGISTRY 列表布线 + 静态 wrapper         → 全部命中 (重构方案的真实形态)

运行
    python prototypes/phase7_registry_studio_probe.py
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

# 模块顶层 import 已编译子图 (金标准写法, 模拟 ``from rag_agent.graph import graph``)
from _phase7_subgraph import graph_a, graph_b


class MainState(TypedDict, total=False):
    """主图状态 (最小占位, 原型不实际运行图)。"""

    value: str


# =========================================================================== #
# 单节点边界测试: 5 种 wrapper 写法
# =========================================================================== #

# --- H1: 模块顶层 import + 静态 wrapper (金标准) ---
async def h1_node(state: Any, config: Any) -> dict[str, Any]:
    """graph_a 是模块全局裸名 → AST 解析为 Pregel。"""
    _ = await graph_a.ainvoke({"value": "x"}, config)
    return {}


# --- H2: 惰性 getter (现有 research_agent 模式) ---
_cache: Any = None


def _get_graph() -> Any:
    """惰性返回子图 (模拟 research_agent 的 _get_research_graph)。"""
    global _cache
    if _cache is None:
        _cache = graph_b
    return _cache


async def h2_node(state: Any, config: Any) -> dict[str, Any]:
    """_get_graph 是模块全局函数; 解析到的值是函数对象, 非 Pregel → 不命中。"""
    g = _get_graph()
    _ = await g.ainvoke({"value": "x"}, config)
    return {}


# --- H3: 闭包工厂, 捕获子图为 nonlocal ---
def make_closure_node(subgraph: Any) -> Any:
    """返回一个闭包, subgraph 作为 nonlocal 被 AST+getclosurevars 捕获。"""

    async def node(state: Any, config: Any) -> dict[str, Any]:
        _ = await subgraph.ainvoke({"value": "x"}, config)
        return {}

    return node


h3_node = make_closure_node(graph_a)


# --- H4: 函数体内 inline import ---
async def h4_node(state: Any, config: Any) -> dict[str, Any]:
    """rg 由 ``from ... import`` 绑定为 local (Store+Load) → 被 loads-stores 排除;
    import 的模块名不是 ast.Name → 不命中。"""
    from _phase7_subgraph import graph_b as rg

    _ = await rg.ainvoke({"value": "x"}, config)
    return {}


# --- H5: 函数体内就地编译 ---
async def h5_node(state: Any, config: Any) -> dict[str, Any]:
    """g 是 local; StateGraph 类是全局但非 Pregel → 不命中。"""
    g = StateGraph(MainState).add_node("n", lambda s: {}).compile()
    _ = await g.ainvoke({"value": "x"}, config)
    return {}


# =========================================================================== #
# REGISTRY 列表布线 (重构方案的真实形态)
# =========================================================================== #


async def sub_a_node(state: Any, config: Any) -> dict[str, Any]:
    """子 agent A 的静态 wrapper (模块顶层 import graph_a)。"""
    _ = await graph_a.ainvoke({"value": "x"}, config)
    return {}


async def sub_b_node(state: Any, config: Any) -> dict[str, Any]:
    """子 agent B 的静态 wrapper (模块顶层 import graph_b)。"""
    _ = await graph_b.ainvoke({"value": "x"}, config)
    return {}


# 显式 registry 清单: 只存布线元数据 + 静态 wrapper 函数引用 (不是工厂产物)
REGISTRY: list[dict[str, Any]] = [
    {"name": "sub_a", "node": sub_a_node, "route_key": "sub_a"},
    {"name": "sub_b", "node": sub_b_node, "route_key": "sub_b"},
]


async def chat_node(state: Any, config: Any) -> dict[str, Any]:
    """主图 chat 节点占位。"""
    return {}


def build_main_from_registry() -> Any:
    """遍历 REGISTRY 布线: add_node(静态函数) + add_edge 回 chat。"""
    wf = StateGraph(MainState)
    wf.add_node("chat_node", chat_node)
    for m in REGISTRY:
        wf.add_node(m["name"], m["node"])  # 传静态函数引用, 非闭包工厂
        wf.add_edge(m["name"], "chat_node")
    wf.set_entry_point("chat_node")
    wf.add_edge("chat_node", END)  # 简化: 不挂条件路由 (不影响子图发现验证)
    return wf.compile(name="MainFromRegistry")


# =========================================================================== #
# 辅助: 编译单节点图 + 报告 subgraphs
# =========================================================================== #


def build_single(node_fn: Any, node_name: str) -> Any:
    """构建一个 START→node→END 的单节点图并编译。"""
    wf = StateGraph(MainState)
    wf.add_node(node_name, node_fn)
    wf.add_edge(START, node_name)
    wf.add_edge(node_name, END)
    return wf.compile(name=node_name + "_graph")


def _sub_names(compiled: Any, node_name: str) -> list[str]:
    """读取 compiled.nodes[node_name].subgraphs 的名字列表。"""
    pregel_node = compiled.nodes[node_name]
    subs = getattr(pregel_node, "subgraphs", []) or []
    return [getattr(s, "name", repr(s)) for s in subs]


def report(tag: str, compiled: Any, node_name: str) -> None:
    """打印某节点的 subgraphs 发现结果。"""
    names = _sub_names(compiled, node_name)
    verdict = "OK 可展开" if names else "空 不可展开"
    print(f"  [{tag:<24}] {node_name:<10} -> subgraphs={names!s:<20} {verdict}")


def main() -> None:
    """跑全部测试并打印结论。"""
    print("=" * 72)
    print("单节点边界测试: 5 种 wrapper 写法")
    print("=" * 72)
    cases = [
        ("H1 顶层import+静态", "H1", h1_node),
        ("H2 惰性getter", "H2", h2_node),
        ("H3 闭包工厂", "H3", h3_node),
        ("H4 体内import", "H4", h4_node),
        ("H5 体内编译", "H5", h5_node),
    ]
    for tag, node_name, fn in cases:
        compiled = build_single(fn, node_name)
        report(tag, compiled, node_name)

    print()
    print("=" * 72)
    print("registry 列表布线 (重构方案真实形态)")
    print("=" * 72)
    main_g = build_main_from_registry()
    report("registry-sub_a", main_g, "sub_a")
    report("registry-sub_b", main_g, "sub_b")

    print()
    print("=" * 72)
    print("结论判定")
    print("=" * 72)
    h1_ok = bool(_sub_names(build_single(h1_node, "H1"), "H1"))
    h2_empty = not _sub_names(build_single(h2_node, "H2"), "H2")
    h3_ok = bool(_sub_names(build_single(h3_node, "H3"), "H3"))
    h4_empty = not _sub_names(build_single(h4_node, "H4"), "H4")
    h5_empty = not _sub_names(build_single(h5_node, "H5"), "H5")
    reg_ok = bool(_sub_names(main_g, "sub_a") and _sub_names(main_g, "sub_b"))

    def line(label: str, ok: bool, expect: str) -> None:
        mark = "PASS" if ok else "FAIL"
        print(f"  {mark}  {label} (期望 {expect})")

    line("H1 顶层import+静态 命中", h1_ok, "命中")
    line("H2 惰性getter 空", h2_empty, "空")
    line("H3 闭包工厂 命中", h3_ok, "命中(探边界)")
    line("H4 体内import 空", h4_empty, "空")
    line("H5 体内编译 空", h5_empty, "空")
    line("REGISTRY 布线 全部命中", reg_ok, "全部命中")

    print()
    if reg_ok and h1_ok and h2_empty:
        print(">> 结论: registry 列表布线 + 静态 wrapper (模块顶层 import) 可被 Studio 发现。")
        print(">>       现有 research_agent 的惰性 getter 模式无法被发现 (H2 空), 重构时一并改为顶层 import。")
    else:
        print(">> 结论: 有未预期项, 见上方 FAIL 行, 需复核机制。")


if __name__ == "__main__":
    main()
