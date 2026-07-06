"""kernel — 跨 agent 共享的横切关注点层。

集中管理 paths / logging / llm / embedder / persistence 等被多个 agent 共用的
基础设施，作为唯一真相源。各 agent 通过 ``from kernel.<module> import ...`` 引用。
"""
