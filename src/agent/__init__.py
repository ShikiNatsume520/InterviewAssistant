"""InterviewAssistant 主 agent 包。

.. note::
   本模块刻意最小化（不在 import 时 eager 加载 ``graph``），以避免循环依赖
   ——R0 重构前 ``from agent import graph`` 会触发整图加载 + LLM 实例化，
   且与子图 ``agent.debug`` 形成循环。横切已上提到 ``kernel``（R0），
   请直接 ``from agent.graph import graph`` / ``from agent.graph import build_main_graph``。
"""

__all__: list[str] = []
