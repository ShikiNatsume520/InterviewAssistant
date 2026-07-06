# interrupt-contracts Specification

## Purpose
TBD - created by archiving change refactor-agent-v1. Update Purpose after archive.
## Requirements
### Requirement: HITL interrupt payload Pydantic 契约固化
系统 SHALL 在 `kernel/contracts.py` 用 Pydantic schema 定义主图↔子图↔前端之间所有 HITL interrupt payload 的契约，包括：计划确认（`PlanConfirmPayload`）、单步确认（`StepConfirmPayload`）、大纲确认（`OutlineConfirmPayload`）、连通性检查提示（`ConnectivityCheckPayload`），以及 resume 决策（`ResumeDecision`：`approve` / `reject` / `suggest`）。

#### Scenario: 子图 interrupt 构造 payload
- **WHEN** `resume_agent` 的 `plan_confirm_node` 调用 `interrupt(...)`
- **THEN** 传入的 payload SHALL 是 `PlanConfirmPayload` 实例（或其 dict 序列化形式），结构由 `kernel.contracts` 定义

#### Scenario: server 序列化 interrupt
- **WHEN** FastAPI `/v1/chat` 检测到 pending interrupt 并向前端推送 SSE `interrupt` 事件
- **THEN** 事件的 `data` SHALL 是由 `kernel.contracts` schema 序列化的 JSON，字段名与结构 SHALL 与重构前兼容（前端 `static/index.html` 不改即可解析）

#### Scenario: resume 值类型校验
- **WHEN** 前端通过 `resume_value` 字段回传决策
- **THEN** server SHALL 能用 `kernel.contracts` 的 `ResumeDecision` 解析 `approve` / `reject` / `{"decision":"suggest","suggestion":"..."}` 三种形态

### Requirement: 协议改动有单一真相源
系统 SHALL 保证 interrupt payload 的字段定义只在 `kernel/contracts.py` 出现。子图 interrupt 构造、server 序列化、前端字段约定 SHALL 共用同一组 Pydantic 类型。

#### Scenario: 新增 payload 字段
- **WHEN** 需要在某 interrupt payload 增加字段
- **THEN** 只需修改 `kernel/contracts.py` 对应 schema，子图与 server 自动获得类型校验，SHALL NOT 需要同步改多处散落定义

