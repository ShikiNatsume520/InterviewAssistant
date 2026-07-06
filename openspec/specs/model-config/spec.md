# model-config Specification

## Purpose
TBD - created by archiving change refactor-agent-v1. Update Purpose after archive.
## Requirements
### Requirement: 模型名与 provider 配置化
系统 SHALL 在 `kernel/config.py` 集中定义聊天模型名、提取模型名、LLM provider 配置（base_url / api_key / model_provider）。`kernel/llm.py` 的 `get_chat_model` SHALL 默认从 config 读取模型名，不得在各 agent 节点内硬编码模型名字符串。

#### Scenario: 改 config 即切模型
- **WHEN** 修改 `kernel/config.py` 或对应环境变量中的模型名
- **THEN** 重启 `langgraph dev` 或 server 后，所有 agent（chat_node / memory / resume / research）SHALL 使用新模型名构造 LLM，SHALL NOT 需要修改 agent 源码

#### Scenario: 无硬编码模型名
- **WHEN** 在 `src/agents/` 下搜索 `"deepseek-v4-flash"` 等模型名字符串
- **THEN** SHALL 仅在 `kernel/config.py` 出现，agent 包内不得出现

### Requirement: LLM 工厂统一入口
系统 SHALL 通过 `kernel/llm.py:get_chat_model(model=None, tools=None)` 提供 LLM 实例构造的唯一入口，按 config 决定 provider 与模型名。各 agent SHALL 调用此工厂，SHALL NOT 直接使用 `init_chat_model` 或 `ChatOpenAI`。

#### Scenario: agent 统一调用工厂
- **WHEN** 任一 agent 节点构造 LLM
- **THEN** 它 SHALL 调用 `kernel.llm.get_chat_model(...)`，返回带（或不带）`bind_tools` 的 `BaseChatModel` 实例

#### Scenario: 非 per-request 注入（选项 A）
- **WHEN** 系统运行
- **THEN** 模型切换 SHALL 通过修改 config + 重启生效（选项 A），SHALL NOT 要求实现 per-request / per-user 注入式多模型（Non-Goal）

