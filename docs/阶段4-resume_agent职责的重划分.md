# 阶段 4：Resume Agent 职责的重划分

> 状态：设计讨论稿，尚未进入正式实现。
>
> 本文是对原“阶段 4：多 Agent 交接契约”的扩展。核心变化是：简历不再进入 Resume Agent 后临时从浏览器选择，而是先成为当前用户拥有的正式产品资源，由主会话、Main Agent 与 Resume Agent共同使用。

## 1. 重划分的原因

当前流程存在上下文断层：

```text
用户向 Main Agent提出模糊请求
        ↓
Main Agent不知道用户有哪些简历，也不知道简历内容
        ↓
Main Agent只能生成宽泛的 intent
        ↓
进入 Resume Agent后才让用户临时选择并上传文件
```

例如用户说“帮我把简历中的多智能体项目优化一下”，Main Agent无法判断：

- 用户指的是哪份简历；
- 简历中是否存在该项目；
- 现有描述是什么；
- 应修改哪个章节；
- 当前请求是否缺少必要信息。

因此，阶段 4 需要先建立用户级简历资源，让 Main Agent在交接前能够确定目标简历并按需检索内容。

## 2. 新的职责边界

### 2.1 前端与后端资源服务

负责确定性的文件操作：

- 上传 Markdown 简历；
- 列出当前用户的简历；
- 指定某份简历作为消息上下文；
- 修改显示名称；
- 删除简历；
- 下载已保存的 Markdown；
- 校验文件类型、大小和资源归属。

LLM 不直接处理上传字节、下载响应、数据库写入或资源权限。

### 2.2 Main Agent

负责理解用户意图与整理交接上下文：

- 知道用户在当前消息中显式指定了哪份简历；
- 在未指定时查询简历列表并遵循默认选择规则；
- 按需对用户简历做关键词检索；
- 区分简历中的既有内容、对话中补充的事实、目标岗位和写作约束；
- 缺少事实时形成待确认问题，不得编造；
- 调用 Resume Agent时提交稳定 `resume_id` 和结构化任务简报。

### 2.3 Resume Agent

负责已指定简历上的编辑流程：

- 接收稳定 `resume_id` 和结构化任务简报；
- 由后端根据 `resume_id` 读取用户拥有的 Markdown；
- 消费目标岗位、写作约束、事实依据和待确认问题；
- 制定计划、执行 `grep_replace`、逐条请求审批；
- 在用户明确保存时提交最终草稿；
- 返回结构化任务结果给 Main Agent。

Resume Agent不再负责在流程入口选择本地文件，也不自行解析任意客户端路径。

### 2.4 草稿与持久化边界

- 正式简历正文保存在用户数据库的 `ResumeDocument.content`。
- Resume Agent工作草稿保存在 `ResumeState.resume_shot`，并随 LangGraph checkpoint 在超级步边界持久化。
- 意外中断后以 checkpoint 中的草稿恢复，不额外创建物理临时文件，避免形成两个恢复来源。
- 源简历在 Resume 会话中始终只读，不允许直接覆盖。
- 用户明确保存退出时，由 Resume 图的确定性持久化节点创建一份新的派生简历；不保存退出时不创建任何正式资源。
- 派生创建使用稳定幂等键，checkpoint 重跑只能得到同一个新 `resume_id`。
- Main Agent不接收完整草稿后再调用工具保存，也不负责删除草稿文件。

## 3. 简历资源模型

内部身份使用 UUID，不使用文件名作为主键：

```text
ResumeDocument
├─ id               UUID，稳定内部身份
├─ principal_id     所属游客或开发人员
├─ original_name    上传时的原始文件名，只作记录
├─ display_name     用户可修改的显示名称
├─ storage_name     上传时间与安全文件名组合的下载建议名
├─ content          Markdown 文本
├─ created_at       上传时间
└─ updated_at       最近保存或重命名时间
```

建议的 `storage_name`：

```text
20260716_143025_后端开发简历.md
```

约束：

- 重命名只修改 `display_name`，不改变 `id`、`original_name` 或资源归属。
- 所有查询必须通过服务端 Session 得到 `principal_id`，不接受客户端自报资源所有者。
- Markdown 内容存入用户数据库，不进入知识向量库。
- 简历关键词搜索与知识库 RAG 是两条独立管道。
- 第一版只接受 `.md` 和服务端允许的 UTF-8 文本大小。

## 4. 简历接口草案

### 4.1 上传

```http
POST /v1/resumes
Content-Type: multipart/form-data

file=<markdown file>
```

响应：

```json
{
  "id": "resume-uuid",
  "originalName": "后端开发简历.md",
  "displayName": "后端开发简历",
  "createdAt": "2026-07-16T14:30:25+08:00",
  "updatedAt": "2026-07-16T14:30:25+08:00"
}
```

### 4.2 列表

```http
GET /v1/resumes
```

只返回元数据，不默认返回所有 Markdown 内容。

### 4.3 读取元数据与内容

```http
GET /v1/resumes/{resumeId}
```

该接口是否向普通前端直接返回全文，将在工作区阶段结合编辑需求确认。Main Agent读取内容不依赖浏览器读取接口，而走服务端受控仓库。

### 4.4 重命名

```http
PATCH /v1/resumes/{resumeId}

{
  "displayName": "Agent 后端岗位简历"
}
```

### 4.5 删除

```http
DELETE /v1/resumes/{resumeId}
```

- 删除前必须二次确认。
- 如果该简历正在活动 Resume 会话中使用，第一版拒绝删除并返回 `409`。
- 删除当前前端选中的简历后，前端立即清除指定状态。

### 4.6 下载

```http
GET /v1/resumes/{resumeId}/download
```

响应为 Markdown 文件，下载文件名使用经过安全处理的 `display_name` 或 `storage_name`。

### 4.7 保存 Resume Agent结果

建议使用后端内部服务完成，不直接开放“客户端任意覆盖正文”的接口。源简历始终只读；只有 Resume 流程在明确保存退出时，才能根据 checkpoint 中的最终草稿创建一份新的派生简历。派生记录保存 `source_resume_id` 和稳定 `derivation_key`，重复执行返回同一结果，不重复创建。

## 5. Main Agent的简历能力

### 5.1 列表工具

```python
list_resumes() -> list[ResumeMetadata]
```

模型只会看到当前用户有权访问的简历。

### 5.2 关键词检索工具

```python
search_resumes(
    query: str,
    resume_id: str | None = None,
) -> ResumeSearchResult
```

返回必须区分 `no_documents`、`no_matches` 和 `matches`，避免把“用户有简历但关键词未命中”误判为“尚未上传简历”：

```json
{
  "status": "matches",
  "items": [
    {
      "resume_id": "resume-uuid",
      "display_name": "Agent 后端岗位简历",
      "matches": [
        {
          "start_line": 24,
          "end_line": 31,
          "content": "Interview Assistant 项目……"
        }
      ]
    }
  ]
}
```

搜索规则：

- 仅做当前用户简历库的关键词匹配；
- 不写入 Chroma；
- 不混入知识库 RAG 结果；
- 保留行号以帮助交接和后续编辑定位；
- `resume_id` 存在时只搜索指定简历。

结果处理规则：

- `no_documents`：Main Agent提示用户通过聊天框入口上传 Markdown 简历；
- `no_matches`：说明现有简历中未找到相关内容，不能错误要求用户重新上传；
- 唯一匹配：可据此确定目标简历；
- 多个匹配：结合名称和上下文缩小范围，仍有歧义时请用户选择。

### 5.3 正文读取工具

```python
read_resume(
    resume_id: str,
    start_line: int = 1,
    end_line: int | None = None,
) -> ResumeReadResult
```

用途：

- 用户显式指定简历后，Main Agent主动阅读相关正文以形成更准确的交接包；
- 对整份简历做宽泛优化时，读取全文或按行分页读取；
- 关键词搜索命中片段不足以理解上下文时，扩大读取范围；
- 区分简历既有事实与用户在对话中新补充的事实。

安全与上下文约束：

- 后端强制验证 `resume_id` 属于当前 Session 用户；
- 返回内容保留行号；
- 单次读取设置最大行数和最大字符数，超出时返回 `has_more` 与下一段起始行；
- 文件上传上限为 1 MiB，但不允许把 1 MiB 全文一次塞进模型上下文；
- Main Agent只在完成交接确有必要时读取，不应每轮无条件加载整份简历。

建议响应：

```json
{
  "resume_id": "resume-uuid",
  "display_name": "Agent 后端岗位简历",
  "start_line": 1,
  "end_line": 180,
  "total_lines": 245,
  "has_more": true,
  "next_start_line": 181,
  "content": "1: # 个人简历\n2: ..."
}
```

### 5.4 Resume Agent调用工具

```python
resume_agent(
    resume_id: str,
    user_request: str | None = None,
) -> str
```

- 前端显式指定的 `resume_id` 作为高优先级上下文提示给 Main Agent，并由后端先校验归属，但后端不替 Main Agent直接选择或启动 Resume Agent；
- Main Agent可使用 `read_resume` 阅读显式指定的简历，再结合用户请求决定交接对象；
- 未显式指定时，Main Agent通过列表、名称、关键词搜索或正文读取确定目标；
- 完全泛化且没有歧义时，Main Agent按系统 Prompt 中的规则优先选择最新上传的简历；
- 当前用户没有简历时不启动 Resume Agent；
- 工具只负责进入子图和传递交接包，不承担最终数据库保存。

### 5.5 本阶段不增加 `create_resume` 工具

阶段 4 的最小闭环是“上传并修改已有 Markdown”，因此 Main Agent暂不获得 `create_resume` / `create_new_resume`：

- 上传由前端和后端资源 API 完成；
- 源简历始终只读，保存退出时创建新的派生简历；
- 最终保存由 Resume 图的确定性节点完成，不能依赖 Main Agent再次调用有副作用的工具；
- 如果未来正式支持“从零生成新简历”，再单独设计幂等的创建工具和空白草稿初始化能力。

## 6. Resume Agent的编辑工具

### 6.1 `request_plan`

保留现有工具。复杂修改、交接目标较多或意图不清晰时，Resume Agent调用它进入计划生成与用户确认流程。该工具本身不修改草稿。

### 6.2 `grep_replace`

保留现有工具，继续作为逐条修改的唯一执行能力：

- 从 `ResumeState.resume_shot` 精确定位唯一原文；
- 生成候选替换并进入逐条审批；
- 用户批准后更新 checkpoint 中的工作草稿；
- 定位失败时向 Resume Agent返回错误，由其使用更完整上下文重试。

阶段 4 不提前增加阶段 5 才需要的 edit ID、章节、理由和 Diff 展示字段。

### 6.3 不暴露为 Resume Agent工具的能力

以下能力由 wrapper、后端仓库或确定性图节点执行：

- 根据 `resume_id` 校验归属并加载正式正文；
- 保存最终草稿；
- 上传、重命名、删除和下载；
- checkpoint 草稿恢复；
- 活动任务锁与幂等控制。

因此阶段 4 的最小完整工具组合固定为：

```text
Main Agent
├─ list_resumes
├─ search_resumes
├─ read_resume
└─ resume_agent

Resume Agent
├─ request_plan
└─ grep_replace
```

## 7. 消息指定简历

聊天请求增加可选字段：

```json
{
  "thread_id": "thread-uuid",
  "message": "帮我优化多智能体项目经历",
  "resume_id": "resume-uuid"
}
```

后端必须先验证 `resume_id` 属于当前 Session 身份，再把“本轮前端指定的简历”作为结构化上下文放入 Main Agent可见输入。不得信任客户端只凭 ID 访问其他用户简历。

该字段表达用户当前选择，不是后端强制路由命令：

1. 前端选择的 `resume_id` 是 Main Agent应优先考虑的强提示；
2. Main Agent可以调用 `read_resume` 获取内容，再判断是否适合当前请求；
3. 用户自然语言明确要求其他简历时，Main Agent应按用户最新指令重新查询和选择；
4. 用户未指定时，Prompt 指导 Main Agent优先考虑最近上传的简历，并通过工具获得真实 ID；
5. 多份简历仍有歧义时必须询问用户，不能由后端静默决定；
6. 当前用户没有简历时，不启动 Resume Agent，提示先上传 Markdown。

后端只承担身份、所有权、ID 有效性与活动任务校验。简历业务选择由 Main Agent根据 Prompt、用户显式选择和工具结果完成；模型只能使用工具真实返回的 ID，不能编造资源 ID。

## 8. Main → Resume 交接契约

```text
ResumeTaskBrief
├─ resume_id
└─ user_request       可为 None
```

其中：

- `resume_id` 标识实际编辑对象。
- `resume_id` 是唯一必填业务字段。
- `user_request` 使用用户原话或忠实的简短概括，可自然包含已经明确的岗位、范围和约束。
- Main Agent只做入口级澄清，不为形成完整需求规格而继续追问。
- Resume Agent读取简历后负责专业澄清；信息不足时主动询问，不得编造。
- `user_id`、`thread_id`、`resume_session_id`、源正文和显示名称由 wrapper 从可信运行上下文与资源仓库注入，不由 Main Agent生成。

## 9. Resume → Main 结果契约

```text
ResumeTaskResult
├─ source_resume_id
├─ output_resume_id   saved 时为新简历 ID；discarded 时为 None
├─ outcome       saved | discarded
├─ summary
└─ display_name
```

完整最终草稿不再通过 ToolMessage塞回 Main Agent上下文。保存成功后，Main Agent只需要知道编辑对象、结果状态和摘要；下载或再次编辑时由资源接口按 `resume_id` 读取正式内容。

保存退出时：

1. Resume Agent完成审批流程。
2. 后端以 `source_resume_id + resume_session_id` 形成稳定幂等键，创建新的派生简历。
3. 源简历正文和 ID 保持不变。
4. Main Agent收到新 `output_resume_id` 和保存结果摘要。
5. 前端可立即下载最新内容。

不保存退出时：

- 工作草稿中的已批准修改不逐条回退，但整个工作草稿不进入正式简历库；
- 不创建新简历，`output_resume_id=None`；
- Main Agent只收到用户已放弃本轮结果，不接收完整草稿。

“已审批不可撤销”只约束活动工作草稿内部，不禁止用户放弃整轮且不保存。

保存和放弃退出只允许从 `hitl_standby` 发起。计划确认与逐条修改审批是独立的待决中断，前端在这些中断期间不展示退出操作，用户必须先完成当前决策。简单修改可以直接进入逐条审批；复杂或多处修改必须先确认计划。放弃退出是终局操作，该草稿之后不再恢复。

## 10. 前端：聊天框指定简历按钮

### 10.1 入口

在聊天输入框附近增加“指定简历”按钮，可采用回形针或文档图标。

未指定时：

```text
[指定简历]  输入消息……                         [发送]
```

指定后显示资源 Chip：

```text
[📄 Agent 后端岗位简历 ×]
[简历]  帮我优化多智能体项目经历              [发送]
```

点击 `×` 只取消消息上下文绑定，不删除简历。

### 10.2 简历列表窗口

点击按钮后打开浮层或 Popover：

```text
选择简历
┌────────────────────────────────────┐
│ 将 Markdown 文件拖到这里上传       │
│ 或点击选择文件                     │
├────────────────────────────────────┤
│ ○ Agent 后端岗位简历      07-16    │
│    [重命名] [下载] [删除]           │
│                                    │
│ ○ 实习简历                07-10    │
│    [重命名] [下载] [删除]           │
└────────────────────────────────────┘
```

交互能力：

- 点击某一行：将其设为当前指定简历；
- 再次点击当前项：保持选中并关闭窗口；
- 拖入单个 `.md` 文件：立即上传；
- 上传成功：插入列表并默认选中新简历；
- 重命名：原地编辑 `display_name`，内部 ID 不变；
- 下载：不改变选中状态；
- 删除：二次确认，成功后从列表移除；
- 空列表：展示上传引导，不展示无意义的选择控件；
- 非 Markdown、多文件或超限文件：在窗口内显示明确错误。

### 10.3 指定状态的生命周期

建议第一版采用“Thread 内持续指定”：

- 选中后，当前 Thread 后续消息都附带该 `resume_id`；
- 用户点击 Chip 的 `×`、选择其他简历、删除简历或切换 Thread 时改变该状态；
- 不因一次消息发送成功自动清除，否则连续讨论同一份简历时容易发生上下文漂移；
- 每个 Thread 的前端指定状态相互独立。

该状态是前端视图状态；真正启动 Resume Agent时，稳定 `resume_id` 会进入 checkpoint。刷新后是否恢复 Thread 的指定 Chip，需要决定是只保存在浏览器，还是持久化为 Thread 的 `selected_resume_id`。

## 11. 典型用户流程

### 11.1 先上传，再模糊提问

```text
用户打开指定简历窗口
  → 拖入 resume.md
  → POST /v1/resumes
  → 后端保存并返回 resume_id
  → 前端自动选中新简历
  → 用户发送“帮我优化多智能体项目”并携带 resume_id
  → Main Agent关键词检索该简历
  → 生成结构化任务简报
  → Resume Agent直接进入该简历的编辑流程
```

### 11.2 未指定但存在简历

```text
用户发送“帮我优化简历”
  → 请求未携带 resume_id
  → Main Agent调用简历列表
  → 后端按规则解析最近上传的简历
  → Main Agent调用 Resume Agent
```

前端和时间线应明确显示系统最终选中了哪份简历，避免静默修改错误文件。

### 11.3 没有任何简历

```text
用户发送“帮我优化简历”
  → 当前用户简历列表为空
  → Resume Agent不启动
  → Main Agent提示用户点击“指定简历”上传 Markdown
```

### 11.4 指定旧简历

```text
用户打开列表并选择“实习简历”
  → 消息请求附带该 resume_id
  → 显式选择优先于“最新简历”规则
  → 后续检索和编辑只针对该简历
```

## 12. 与原流程的变化

原流程：

```text
Main只传 intent
  → Resume Agent启动
  → resume_select Interrupt
  → 浏览器临时读取文件并提交全文
```

新流程：

```text
简历预先上传为用户资源
  → Main确定 resume_id并按需检索
  → Main传结构化任务简报
  → Resume wrapper从用户仓库读取 Markdown
  → Resume Agent直接进入计划或编辑流程
```

原 `resume_select` Interrupt 在正常路径中退役。多简历选择发生在主会话和 Main Agent阶段，不在 Resume Agent出现后重复选择。

## 13. 阶段拆分建议

由于该调整同时影响数据资源、Agent 交接和前端输入区，建议阶段 4 内部拆成两个连续验收切片，但仍作为同一阶段讨论：

### 4A：简历资源基础

- 用户级简历表与归属校验；
- 上传、列表、重命名、删除、下载；
- 前端指定简历窗口；
- 当前消息携带 `resume_id`；
- Main Agent列表与关键词搜索能力。

### 4B：结构化交接与模式状态

- Main → Resume 任务简报；
- Resume wrapper按 `resume_id` 加载简历；
- Resume → Main 结构化结果；
- `active_mode` / `active_agent`；
- 输入框接收者提示和 Agent 进入/退出事件。

阶段 5 再实现中间工作区、Diff、edit ID 和 React 审批卡片。

## 14. 尚需确认的决策

1. **指定状态是否持久化**：推荐 Thread 内持续指定，并由后端保存 `selected_resume_id`，使刷新后 Chip 与 Main Agent上下文一致。
2. **默认简历规则**：推荐未显式指定时选择最近上传的简历，但必须在时间线明确告知用户选中了哪份。
3. **放弃退出（已确认）**：允许放弃整个工作草稿且不创建新简历；已批准修改只是在活动草稿内部不能逐条回退。
4. **保存策略（已确认）**：源简历永不覆盖；保存退出创建一份带 `source_resume_id` 的新派生简历，并通过稳定幂等键防止 checkpoint 重跑生成重复副本。
5. **删除策略**：推荐活动 Resume 会话引用的简历禁止删除；其他简历永久删除且不可恢复。
6. **文件大小限制**：需要确定第一版 Markdown 上限，建议 1 MiB，已足够覆盖普通文本简历。

## 15. 验收目标

- 两个用户不能列出、读取、检索、修改、下载或删除彼此简历。
- 用户可以在聊天输入区上传、选择、重命名、下载和删除 Markdown 简历。
- 重命名不改变内部 `resume_id`。
- 消息显式指定的简历不会被默认最新简历覆盖。
- Main Agent能在交接前检索目标简历的相关内容。
- 没有简历时不会错误启动 Resume Agent。
- Resume Agent出现时已经知道正在修改哪一份简历。
- 简历不进入知识库向量索引。
- 保存退出和 checkpoint 重跑不会生成重复派生简历，源简历始终保持不变。
