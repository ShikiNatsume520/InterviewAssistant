"""主图 agent 包。

.. note::
   本模块刻意最小化（不在 import 时 eager 加载 ``graph``），以避免循环依赖。
   横切已上提到 ``kernel``，请直接
   ``from agents.main.graph import graph`` / ``from agents.main.graph import build_main_graph``。
"""

__all__: list[str] = []
