# 深研资料: loop engineering 概念在AI Agent领域的定义、内涵与实践：loop engineering 指Agent自主循环直到达成目标的工作模式（类似GOAT模式），无需人类参与；该概念的提出者、提出时间、相关讨论热度；与传统Agent循环（agentic loop）、自我改进循环、反射模式的区别与联系；loop engineering在主流Agent框架（LangGraph、AutoGPT等）中的具体实现方式；该概念为何在2024-2025年成为热门话题

## 定义与内涵

- **核心定义**：Loop engineering 是设计引导 AI Agent 自主运行的提示系统，而非手动输入每条提示的实践。其核心思路是构建一个循环：Agent 自动寻找任务、执行、验证、记忆，无需人类参与。 [1][3]
- **工作模式**：Agent 自主循环直到达成目标，类似 GOAT（Generative Offensive Agent Tester）模式中多轮自动攻击链，但 loop engineering 面向正面任务（如代码修复、问题分派），强调递归目标设定和自主验收。 [1][8][10]
- **三层范式叠代**：Addy Osmani 提出底层为 Prompt Engineering（优化单次输入），中层为 Agent Harness Engineering（创建工具、规则、检查环境），顶层为 Loop Engineering（设计自动指令机制，运行于 Harness 之上）。 [4][10]
- **核心循环**：act → observe → reason → repeat。Agent 感知当前状态，推理计划，执行动作，观察结果，然后根据目标决定是否继续。 [4][5]
- **判别条件**：一个任务是否适合用 loop 处理需满足三要素——Repetitive（频率足够高）、Reviewable（可定义可验证的“完成”状态）、Valuable（输出价值超过 token 成本）。 [4]
- **定义来源**：Addy Osmani 的博客将 Loop Engineering 定义为“递归目标设定”：一次定义目标后，AI 自动循环运行直至完成。 [10]

## 提出者、提出时间与流行背景

- **概念提出与命名**：术语“Loop Engineering”由 Google Chrome 工程负责人 Addy Osmani 在 2026 年 6 月撰文普及并正式命名。 [1][10]
- **思想先驱**：Peter Steinberger（PSPDFKit/Nutrient 创始人）于 2026 年 6 月 7 日提出“你应该设计循环来提示你的 Agent，而不是手动提示”。 [1][4]
- **业界共鸣**：Anthropic Claude Code 创建者 Boris Cherny 表示：“我不再提示 Claude 了。我运行着循环，它们负责提示 Claude 并决定下一步做什么。我的工作是写循环。” [1][4][11]
- **早期实践**：Geoffrey Huntley 在 2026 年 1 月提出“ralph loop”：反复给 Agent 相同目标，让其自主运行数小时，自主查找、计划并解决工作。 [11]
- **学术响应**：Andrew Ng 于 2026 年 6 月 30 日提出内层 Agentic Coding Loops（分钟级）嵌套在开发者反馈（小时级）和外部用户反馈（天级）中。 [1]
- **讨论热度**：2026 年 6 月 25 日左右在 X（Twitter）上迅速引爆工程社区讨论，Udemy 推出约 1 小时视频课程，Anthropic 和 OpenAI 分别发布官方循环指南。 [1][3][11]
- **背景时间线**：Prompt Engineering 主导 2022–2024，Context Engineering 兴起于 2025，Harness Engineering 出现在 2025 年末，Loop Engineering 于 2026 年 6 月成为新范式。 [10][11]

## 五大核心组件（+ Memory）

- **Trigger（触发）**：启动循环的条件，可以是定时任务（cron）、事件（GitHub issue 开立）、人类指令，或其他 Agent 完成任务信号。 [1]
- **Goal（目标）**：可验证的终点状态，例如“所有测试通过”“零未处理的 P1 问题”“包体积小于 200KB”。Claude Code 的 `/goal` 命令要求预定义结束条件。 [1][3]
- **Actions（动作）**：Agent 可执行的操作，包括读/写文件、运行 bash 命令、调用 API 和 MCP 服务器、生成子 Agent。Claude Code 的 hooks 可拦截和门控这些动作。 [1]
- **Verification（验证）**：确保目标已达成的机制，如运行测试检查退出码、监管 Agent 阅读最终状态、第二模型审查差异、CI 管道通过。Claude Code 的 `/goal` 使用监管架构生成独立会话来验证。 [1]
- **Memory（记忆）**：跨会话状态管理，包括 CLAUDE.md（稳定项目上下文，每次加载）和外部数据库/向量存储（跨会话持久化）。 [1][3]
- **完整循环示例**：早晨 Issue 分类循环——Trigger：每工作日 8 点；Goal：所有 P1 Issue 具有负责人和计划评论；Actions：通过 MCP 读取 GitHub Issue、写评论、分配标签；Verify：检查零无负责人的 P1；Memory：记录本周已处理的 Issue 日志。 [1]

## 与传统 Agent 循环、反射模式等区别

- **与传统 Agentic Loop 的关系**：传统 Agentic Loop 是基础模式（observe → reason → act），但 loop engineering 将其提升为系统设计层面，强调触发、目标、验证、记忆等元结构，而非仅运行时推理循环。 [5][4]
- **与自我改进循环的区别**：自我改进循环通常指模型在训练或推理中根据自身输出调整；loop engineering 关注外部系统编排，通过工具、验证、记忆实现自动化，不依赖模型内省。 [4][5]
- **与反射模式的区别**：反射模式下 Agent 仅针对单次输出做自我检查（如 ReAct 中的反思）。Loop engineering 包含全局循环，可跨多轮、跨会话、跨 Agent 协作，并引入专用验证器或监管 Agent。 [1][4]
- **与 Prompt Engineering 的对立**：Prompt Engineering 优化单次对话措辞；Loop Engineering 优化自动决定“下一步指令、时机、验收标准”的机制。Loop Engineering 是“提示设计”的自动化升级版。 [10][11]
- **与 Harness Engineering 的关系**：Harness Engineering 创建 Agent 工作环境（工具、规则、检查系统），Loop Engineering 运行于其上，控制循环何时启动、如何完成目标。 [10]
- **固定自动化 vs 智能体循环**：固定自动化是线性路径，出错即卡住；Agent 循环是迭代的，能动态调整步骤次数与顺序，像人类一样按难度分配精力。 [5]

## 在主流 Agent 框架中的具体实现

### LangGraph 实现方式

- **低层原语与状态管理**：LangGraph 通过状态（State）驱动循环，每个节点更新状态，条件边路由器（Router）决定下一步。 [6][7]
- **防止无限循环**：内置 recursion_limit 默认 25 步（硬异常）；生产实践建议在 State 中注入 steps 计数器（TTL），利用条件边检查超限，路由到 fallback 节点。 [7]
- **监督与语义缓存**：可添加专用 Critic/Supervisor 节点（使用较小模型评估轨迹），并引入语义缓存检测重复工具调用，注入负反馈迫使新推理路径。 [7]
- **持久化与流式**：内置 memory 存储跨会话上下文，支持逐 token 流式输出实时反馈。 [6]
- **遥测工具**：LangSmith 平台调试每个 Agent 决策，帮助诊断循环瓶颈。 [6][7]

### AutoGPT / OpenAI Codex 实现方式

- **OpenAI Codex 的 Automations 模式**：Codex 提供 Automations 标签页，用于配置仓库、提示、定时计划、沙箱环境，结果进入 Triage。用户无需手动触发。 [4]
- **循环触发示例**：Codex 的 Automations 支持基于 repo 事件或定时计划启动循环，Agent 自主完成任务后结果分类处理。 [4]
- **隔离并发**：Codex 自动使用 Git worktrees 为每个 Agent 创建独立分支和工作目录，避免文件覆盖和合并冲突。 [4]
- **技能文件夹**：Codex 支持 SKILL.md 文件，描述 Agent 能力，agent 根据任务描述自动加载匹配技能。 [4]

### Claude Code 实现方式

- **内置命令与任务系统**：Claude Code 提供 `/loop`、`/goal`、`/bg`（后台任务）命令，支持定时任务和 hooks。 [1][4]
- **监管式验证**：`/goal` 命令启动一个独立的监督会话来验证目标是否达成，防止 Agent 伪造结果。 [1]
- **业务循环示例**：从真实反馈自动实现新功能：拉取新请求并分类 → Claude 编写实现 → CodeRabbit 在循环内审查 → 运行测试 → 等待 CI → 自动合并 → post-merge 部署检查。 [11]
- **记忆与上下文**：通过 CLAUDE.md 和外部状态文件（纯 Markdown 日志）实现跨恢复运行，项目约定存在 skill 中。 [1][11]
- **多 Agent 协作**：支持 Writer 与 Reviewer 分离（不同 Agent 或不同模型），Grader 只需不同而非更聪明。 [4]

### 其他框架与通用实践

- **MetR 基准**：METR 发现 Agent 自主完成任务长度每 7 个月翻倍（2019–2025），MirrorCode 基准中 Agent 自主重写 16,000 行代码工具包。 [12]
- **Ralph Loop**：简单 bash 脚本 + 状态文件，让新 Agent 在上下文窗口耗尽时无缝接替，进展通过 git 和文件系统保存。 [11]
- **通用循环架构**：单 Agent 重复研究-草稿-自审-修订（小规模）；Orchestrator 拆分目标给 Specialist，每个 Specialist 使用自己 Helper（大规模）。 [4]

## 为何在 2024–2025 年成为热门话题

- **技术成熟度跃迁**：2024 年 10 月 Google 约 25% 新代码由 AI 生成，2025 年末升至约 50%，2026 年 4 月达约 75%。AI 代码生成比例激增使得手动提示不可持续，催生自动化循环需求。 [12]
- **模型自主能力提升**：METR 发现从 2019 到 2025，Agent 自主完成任务的可靠长度每 7 个月翻倍。2025–2026 年 Claude Code 等产品实现超过 80% 的代码自主编写，使得“设计循环”成为必要。 [12]
- **范式转移信号**：Anthropic 报告工程师每季度代码产出约为 2024 年的 8 倍，一次自主运行修复 800 余个 API 错误。大规模自主工作迫使社区重新思考人与 AI 的交互方式。 [12]
- **产业领袖发声**：2026 年 6 月 Peter Steinberger 和 Boris Cherny 的公开言论迅速引爆讨论，Addy Osmani 的命名和体系化总结使术语广泛接受。 [1][4][11]
- **框架与产品就绪**：OpenAI Codex、Claude Code 以及 LangGraph 在 2025–2026 年陆续引入循环相关的内置功能（Automations、/goal、/loop、skill 系统等），使工程落地成为可能。 [1][3][4][6]
- **概念成熟度爬升**：从 Prompt Engineering（2022–2024）→ Context Engineering（2025）→ Harness Engineering（2025–2026）→ Loop Engineering（2026），每一步都建立在之前层之上，2025 年的 Context Engineering 和 Harness Engineering 为 Loop Engineering 铺平了道路。 [10][11]

## 来源

- https://explainx.ai/blog/what-is-loop-engineering-ai-agents-2026
- https://www.besthub.dev/articles/what-is-loop-engineering-in-ai-agents-definition-components-and-use-cases-explained-d20bb898b4cd
- https://lushbinary.com/blog/loop-engineering-ai-coding-agents-guide/
- https://www.firecrawl.dev/blog/loop-engineering
- https://www.taskade.com/blog/agentic-workflows-explained
- https://www.langchain.com/langgraph
- https://rajatpandit.com/ai-engineering/optimizing-langgraph-cycles/
- https://www.giskard.ai/knowledge/goat-automated-red-teaming-multi-turn-attack-techniques-to-jailbreak-llms
- https://www.tredence.com/blog/ai-agents-vs-ai-assistants
- https://note.com/genelab_999/n/nf357e65cadce?hl=en
- https://www.coderabbit.ai/blog/loop-engineering
- https://lushbinary.com/blog/loop-engineering-future-of-software-development/