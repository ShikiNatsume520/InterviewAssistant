# resume_agent 设计实现文档（定稿实施版 v3）

> 核心范式：**interrupt 信号就是 POST 的回复**。放弃流式。
> 结构：① 核心范式 → ② 案例走查 → ③ 节点与中断 → ④ 后端接口 → ⑤ 前端接口 → ⑥ 阶段划分。

---

## 一、核心范式

**后端驱动 + interrupt 即回复**。每次 `POST /v1/chat` = invoke 一次主图 → 跑到下一个 interrupt 或 END → **回复就是那个 interrupt 的 payload**（或 done）。前端只读回复，不拉取、不流式、不主动唤起。

映射到 LangGraph：
- `interrupt(value)` = 后端主动挂起要输入（决定何时要输入），状态存 checkpointer，GraphInterrupt 向上透传，主图也挂起存 checkpointer。
- 后端 invoke 跑到 interrupt → 回复 payload = interrupt value。
- 前端据 payload 的 `phase` 弹对应框 = 被动响应。
- `Command(resume=value)` = 前端把值塞回后端（invoke 同一主图 thread 恢复），LangGraph 据当前挂起点自动路由到对的 interrupt——**所有 interrupt 共用 `/v1/chat` 一个恢复接口**，前端不指定是哪个 interrupt，只填当前 payload 要求的字段。
- 节点收到 value 后自己 `return {messages:[HumanMessage(...)]}` 注入 = "后端拉取到的输入"进 messages（**前端不灌 messages**）。

**放弃流式**：chat_node 的文本回复**不流式**。chat_node 每次决策后必经一个中断节点（调 grep_replace→approve / 调 request_plan→plan_confirm / 无 tool_call→hitl），chat_node 的文本输出存 state，由该中断节点的 payload `llm_text` 字段带出，前端从中断回复里取展示。

**intent 走 URL**：主图精炼的 query 由 wrapper 灌入子图 state（`intent` 字段）。第一个中断 select_resume 的 payload 带 `intent` → 主页收到 → 跳 /resume?thread=T&intent=... → /resume 从 URL 取展示为右栏首条。后端不额外推 intent，前端不拉取。

**后端不碰文件 IO**：前端用 `webkitdirectory` 扫**用户本地目录**读简历（后端不知本地路径）。select_resume 不读文件（直接用前端传的 `{resume_file, resume_shot}`），persist 不写文件（最终 shot 经 `done` 回复返前端，前端写回本地）。

**主图不挖子图 state**：子图 state 与主图 state 隔离。前端所需数据全部来自"中断回复 payload"或"URL 传参"或"前端自持"，无一来自主图挖子图 state.values。

| 前端所需 | 来源 |
|---|---|
| 首条 intent | select_resume 回复 payload 的 `intent` 字段 → 主页跳转带入 URL → /resume 从 URL 取 |
| interrupt 内容（approve 的 before/after/edits、hitl 的 summary、plan 的 plan） | 各中断回复 payload 自带 |
| chat_node 文本回复 | 各中断回复 payload 的 `llm_text` 字段 |
| resume_shot（左栏） | 前端自维护：select 时自己读文件持有；approve 时用 payload before/after 更新本地 |
| HumanMessage（new_request / 建议 / 选区） | 前端自己知道（用户在弹框打的），前端自显示 |

---

## 二、案例走查

用户在主页输入「请帮我修改简历，把八爪科技大学改成测试科技大学」，到完成跳回主页。

| # | 触发方 | 动作 / 数据流 | POST 回复 |
|---|---|---|---|
| 1 | 用户 @ 主页 | `POST /v1/chat {user_id, thread_id, message}` | — |
| 2 | 后端 | 无 pending interrupt → `{messages:[HumanMessage(message)], user_id}` 启动主图 | — |
| 3 | 主图 chat_node | LLM 识别简历意图，整理扩充 query → 调 `resume_agent(intent=query)` | — |
| 4 | route_after_chat | `Send("resume_agent", {tool_call:{args:{intent}, id}})` | — |
| 5 | wrapper | 读 intent → 灌 `messages=[HumanMessage(intent)]` + 存 `intent` 进 state → `ainvoke` resume 子图 | — |
| 6 | select_resume_node | `interrupt({phase:resume_select, intent, llm_text:""})` → 透传主图挂起 | — |
| 7 | 后端 | ainvoke 跑到 interrupt | **{interrupt:{phase:resume_select, intent}}** |
| 8 | 主页 | 收 resume_select → `location.href="/resume?thread=T&intent=<encoded>"` | — |
| 9 | /resume 加载 | URL 取 thread+intent（右栏首条）；弹选目录框 `<input webkitdirectory>` → 用户选目录 → JS 扫 `.md` → 选 sample.md → FileReader 读内容 | — |
| 10 | /resume | `POST /v1/chat {thread_id:T, resume_value:{resume_file:"sample.md", resume_shot:"<内容>"}}` | — |
| 11 | 后端 | 有 pending interrupt → `Command(resume={resume_file, resume_shot})` → 穿透到 select_resume | — |
| 12 | select_resume | 收到 value → `return {resume_shot, resume_file}` | — |
| 13 | init_node | 设 `last_shot=""` | — |
| 14 | chat_node | LLM 据 shot+intent 决策 → 简单修改 → 调 `grep_replace(grep_target="八爪科技大学", replace_content="测试科技大学")`；文本存 `last_llm_text` | — |
| 15 | route_after_chat | grep_replace → edit_executor | — |
| 16 | edit_executor | 串行执行波 → new_shot + last_shot（波前快照）+ tool_msgs | — |
| 17 | approve_node | `interrupt({phase:resume_approve, before, after, edits, llm_text})` → 透传主图挂起 | — |
| 18 | 后端 | ainvoke 跑到 interrupt | **{interrupt:{phase:resume_approve, before, after, edits, llm_text}}** |
| 19 | /resume | 右栏渲染 llm_text；左栏 renderResume(after, edits) 标红绿；显示批准/拒绝/建议栏 | — |
| 20 | 用户点「批准」 | `POST /v1/chat {thread_id:T, resume_value:{decision:"approve"}}` | — |
| 21 | Command 穿透 | approve 收到 → `return {}`（不撤销） | — |
| 22 | chat_node | LLM 看修改完成 → 无 tool_call → 回复总结 → `last_summary` + `last_llm_text` | — |
| 23 | route_after_chat | 无 tool_call → hitl_standby | — |
| 24 | hitl_standby | `interrupt({phase:resume_hitl, summary, llm_text})` → 透传主图挂起 | — |
| 25 | 后端 | ainvoke 跑到 interrupt | **{interrupt:{phase:resume_hitl, summary, llm_text}}** |
| 26 | /resume | 右栏渲染 llm_text（总结）；显示新意图输入框 + 退出按钮 | — |
| 27 | 用户点「退出」→「保存并退出」 | `POST /v1/chat {thread_id:T, resume_value:{action:"exit", save:true}}` | — |
| 28 | Command 穿透 | hitl 收到 → `return {save:true}` | — |
| 29 | route_after_hitl | save=true → persist | — |
| 30 | persist | 不写文件，`return {}` | — |
| 31 | persist → END | 子图结束 | — |
| 32 | wrapper | ainvoke 返回 `{last_summary, resume_shot}` → 产 ToolMessage+SystemMessage 反馈 + 回填 current_resume → 主图恢复 | — |
| 33 | 主图 chat_node | 收 ToolMessage → 产最终总结 | — |
| 34 | 后端 | 无 pending interrupt | **{done:{last_message, current_resume}}** |
| 35 | /resume | 收 done → current_resume 写回用户本地 → `location.href="/"` 自动跳回主页 | — |

---

## 三、resume_agent 节点与中断

### 拓扑

```
START → select_resume_node(interrupt A) → init_node → chat_node ★
  ├─ grep_replace(可一波多个) → edit_executor → approve_node(interrupt B)
  │     ├─ approve → chat_node
  │     ├─ reject → 撤销整波 → chat_node
  │     └─ suggest(+suggestion+selection?) → 撤销整波+注入建议 → chat_node
  ├─ request_plan → plan_node → plan_confirm_node(interrupt C)
  │     ├─ suggest(+suggestion+selection?) → plan_node(重规划)
  │     └─ approve → chat_node
  └─ 无 tool_call(总结) → hitl_standby_node(interrupt D)
        ├─ new_request(+request+selection?) → chat_node
        ├─ exit(save=true) → persist → END
        └─ exit(save=false) → END
```

### ResumeState 字段

| 字段 | 说明 |
|---|---|
| `intent` | 主图精炼的 query（wrapper 灌入），select payload 带 |
| `resume_shot` | 当前简历 markdown |
| `resume_file` | 文件名（前端传） |
| `last_shot` | 波前快照（approve 撤销用） |
| `plan` | 计划步骤列表 |
| `last_summary` | chat_node 总结（hitl payload 带） |
| `last_llm_text` | chat_node 最近一次文本输出（各中断 payload 带） |
| `save` | 退出信号 |
| `messages` | add_messages reducer |

### 节点 + 中断 + payload + 期望 resume_value

| 节点 | 中断 | payload | 期望 resume_value |
|---|---|---|---|
| `select_resume_node` | A | `{phase:"resume_select", intent, llm_text:""}` | `{resume_file, resume_shot}` |
| `init_node` | — | — | — |
| `chat_node` | — | — | —（文本存 last_llm_text） |
| `edit_executor_node` | — | — | — |
| `approve_node` | B | `{phase:"resume_approve", before, after, edits:[{grep_target,replace_content}], llm_text}` | `{decision:"approve"\|"reject"\|"suggest", suggestion?, selection?}` |
| `plan_node` | — | — | —（补 ToolMessage 响应 request_plan） |
| `plan_confirm_node` | C | `{phase:"plan_confirm", plan, llm_text}` | `{decision:"approve"\|"suggest", suggestion?, selection?}` |
| `hitl_standby_node` | D | `{phase:"resume_hitl", summary, llm_text}` | `{action:"new_request", request, selection?}` / `{action:"exit", save:bool}` |
| `persist_node` | — | — | —（不写文件，return {}） |

### 关键机制

- **edits 红绿**：approve_node 的 `_collect_edits` 从 messages 倒找最近带 tool_calls 的 AIMessage，提取所有 grep_replace 的 `{grep_target, replace_content}` 对放 payload。前端 grep_target 标红、replace_content 标绿。
- **suggest 选区增强**：任何 interrupt 的 suggest 分支都能带 `selection`（用户在 shot 里选中的原文片段）。节点把 selection 注入 HumanMessage。
- **last_llm_text 流转**：chat_node 每次产出文本存 `last_llm_text`（含调工具时的 content）；下一个中断节点从 state 读塞 payload。
- **plan_node ToolMessage**：chat_node 调 request_plan 产出的 AIMessage(tool_calls) 必须被 ToolMessage 响应，否则 OpenAI 400。plan_node 补一条响应 request_plan 的 tool_call_id。

### wrapper（resume_agent_node）

主图节点，纯透传：读 `state["tool_call"].args.intent` → 灌 `messages=[HumanMessage(intent)]` + 存 `intent` 进 state → `ainvoke resume_graph`（继承父 checkpointer）→ catch GraphInterrupt 透传 → 子图 END 后据 `result.last_summary` 产 ToolMessage+SystemMessage 反馈 + 回填 `current_resume`。

---

## 四、后端接口（2 个）

### `POST /v1/chat`（启动主图 / 恢复中断，统一，非流式）

**请求** `ChatRequest`：`{user_id, thread_id, message?, resume_value?}`（启动传 message，恢复传 resume_value，优先于 message）。

**行为**：
- 无 pending interrupt → `{messages:[HumanMessage(message)], user_id}` 启动主图
- 有 pending interrupt → `Command(resume=resume_value ?? message)` 恢复
- `graph.ainvoke(input, config)`（**非流式**），跑到下一个 interrupt 或 END

**回复**（JSON，非 SSE）：
- 挂起 → `{"interrupt": {phase, ...payload}}`
- 结束 → `{"done": {"last_message": "...", "current_resume": "...", "citations": []}}`

### 其他端点

| 端点 | 说明 |
|---|---|
| `GET /` | 返回 index.html |
| `GET /resume` | 返回 resume.html |
| `GET /health` | 健康检查 |

### 撤销（阶段1）

撤 `/v1/resume` 独立端点 + `RESUME_CHECKPOINT_DB` + `resume_saver` + lifespan 独立编译 + `ResumeRequest` + `_resume_state_snapshot` + 流式（astream/subgraphs=True/SSE）。保留模块级 `graph = build_resume_workflow().compile(name="resume_agent")`（wrapper 用，无 checkpointer，运行时继承父图）。

---

## 五、前端接口

### `static/index.html`（主页）

**发送**：`POST /v1/chat {user_id, thread_id, message}`。

**回复处理**：
- `interrupt`(phase=resume_select) → `location.href="/resume?thread="+threadId+"&intent="+encodeURIComponent(interrupt.intent)`
- `interrupt`(phase=plan_confirm 等) → 主图若有则渲染（保留现有）
- `done` → 渲染 citations / last_message

**清理**：`renderInterrupt` 删死分支 `step_confirm`。

### `static/resume.html`（resume 专用页，纯响应式）

**初始化**：URL 取 `thread` + `intent`（右栏首条 HumanMessage）。

**选简历**（页面加载即弹）：`<input type="file" webkitdirectory>` → 选目录 → JS 遍历 `input.files` 过滤 `.md` → 列表 → 选 → FileReader 读 → 发 `POST /v1/chat {thread_id, resume_value:{resume_file, resume_shot}}`。

**发送**：`POST /v1/chat {thread_id:thread, resume_value:<见下>}`（无 message）。

| 场景 | resume_value |
|---|---|
| 选定简历 | `{resume_file, resume_shot}` |
| approve | `{decision:"approve"}` |
| reject | `{decision:"reject"}` |
| suggest | `{decision:"suggest", suggestion, selection?}` |
| plan_confirm approve | `{decision:"approve"}` |
| plan_confirm suggest | `{decision:"suggest", suggestion, selection?}` |
| hitl new_request | `{action:"new_request", request, selection?}` |
| hitl exit | `{action:"exit", save:bool}` |

**回复处理**：
- `interrupt`(resume_approve) → 右栏渲染 llm_text；左栏 renderResume(after, edits) 标红绿；批准/拒绝/建议栏（建议可带选区）；本地 shot 更新=after(approve后)/before(reject/suggest后)
- `interrupt`(plan_confirm) → 右栏渲染 llm_text；展示计划 + 批准/建议栏
- `interrupt`(resume_hitl) → 右栏渲染 llm_text（总结）；新意图输入框 + 退出按钮（弹保存/不保存）
- `interrupt`(resume_select) → 弹选目录框
- `done` → current_resume 写回用户本地；`location.href="/"` 自动跳回主页

**右栏 messages 来源**：首条=URL intent；AI 回复=各中断回复 llm_text；用户输入=前端自显示。

---

## 六、阶段划分（按 CLAUDE.md 分阶段协商）

机制核心（interrupt 透传 / Command(resume) 穿透 / 主图嵌套子图）已验证（phase15）。本范式撤流式、改 interrupt 即回复、intent 走 URL、llm_text 走 payload，无新增关键技术风险，可省原型：

1. **后端**：state 加 intent/last_llm_text + contracts 简化/补字段 + select 收窄 + chat_node 存 last_llm_text + 各中断带 llm_text + persist 不写文件 + wrapper 存 intent + app 撤独立端点改非流式 + 删已废测试
2. **前端 index.html**：resume_select 跳转（带 intent 入 URL）+ 删 step_confirm 死分支
3. **前端 resume.html**：URL 取 thread+intent + webkitdirectory 扫本地目录 + 纯响应式（按 interrupt phase 弹框 + llm_text 渲染 + suggest 带选区）+ 退出写回本地 + 自动跳回主页
4. **联调 + 测试**：langgraph dev 真前端跑通全链路；ruff + mypy --strict(src/) + pytest

整体完成后统一 git 存档。
