# 罗志杰

> <span class="icon">&#xe60f;</span> `18379159960`&emsp;&emsp;
> <span class="icon">&#xe7ca;</span> `a2708396741@outlook.com`&emsp;&emsp;

<img class="avatar" src="./xjpic.jpg">

## &#xe80c; 教育经历

<div class="entry-title">
    <h3>北京交通大学(211) - 本科 - 计算机科学与技术 </h3> 
    <p>2021.09 - 2025.06</p>
</div>

<div class="entry-title">
    <h3>北京交通大学(211) - 硕士 - 计算机技术</h3> 
    <p>2025.09 - 至今</p>
</div>

## &#xe673; 研究方向
本科毕设《结合遗传算法和强化学习的无人机路径规划方法设计与实现》  
研究生课题《结合大语言模型的奖励分配框架——利用大模型或者Agent语义推理与动作轨迹语义回溯，解决长时序决策中的信用分配难题》

## &#xe635; 项目经历

<div class="entry-title">
    <h3>BasicAgent</h3>
    <a href="https://github.com/ShikiNatsume520/BasicAgent">link</a>
</div>

* **技术栈：** Python, LiteLLM, Pydantic, JSONL, asyncio

轻量级无状态 Agent 核心引擎，仿 Claude Code 设计，支持自主工具调用、多级上下文压缩与流式解析。

- **构建三层压缩管线（Snip→MicroCompact→AutoCompact）**；自研补丁解决“MicroCompact触发机制”，近似 Claude 缓存机制，保障长程任务稳定。
- **双轨消息系统**：设计 BAMessage 统一抽象层，封装 7 种语义角色与多模态内容；通过适配器模式兼容主流 LLM API，实现内部逻辑与协议转换的解耦。
- **会话恢复**：采用 JSONL + 追加写模式，结合 UUID 去重与微批（100ms）刷盘；支持 resume_session 精准重建历史上下文，实现无缝中断恢复。


<div class="entry-title">
    <h3>基于 LangGraph 的多智能体系统——InterviewAssistant</h3> 
    <a href="https://github.com/ShikiNatsume520/InterviewAssistant">link</a>
</div>

* **技术栈：** Python, LangGraph, ChromaDB, FastAPI, SQLite, httpx

基于 LangGraph 编排的 RAG 与多智能体系统，支持双轨索引、置信度驱动的混合检索，以及含人工介入的简历优化与自主深研子图。
- **可插拔 Agent 架构**：采用 @tool包装子图，统一接入 Main Agent 调度；设计 Registry 路由表实现组件热插拔；通过 Wrapper 节点隔离执行环境，解决子图状态映射冲突。
- **调试与中断机制**：基于 LangGraph Studio 逆向分析，确立子图静态发现规范；利用 interrupt()透传实现跨层级人机协同（HITL），依托 Checkpointer 维持子图驻留状态，支持断点续执行。
- **智能检索系统**：研发双模 RAG 架构——Chroma 行级向量索引负责片段召回，index.md语义索引负责主题导航；支持关键词/语义互斥检索，结果附带行号引用与置信度评分，自动触发深研流程。
- **多智能体系统**：实现含网络爬虫的自主深研子图（上限 15 页），产出自动回流知识库；开发简历优化助手，智能调用plan进行复杂规划，设计快照机制实现单步回退。

## &#xecfa; 专业技能

- **强化学习与决策智能**：掌握 PPO、DDPG、GRPO 等主流算法原理以及项目经验。
- **编程语言**：熟悉 Python、C++、Java，具备良好的工程化编码习惯与类型注解实践。
- **AI 框架与生态**：熟练使用 LangGraph、LangChain 进行复杂工作流编排；熟悉 LiteLLM 多模型接入与 Pydantic 数据校验。
- **数据存储与检索**：掌握 ChromaDB、SQLite，熟悉向量检索、混合检索策略及索引优化。
- **算法与基础**：扎实的数据结构与算法功底，具备系统性能分析与调优能力。