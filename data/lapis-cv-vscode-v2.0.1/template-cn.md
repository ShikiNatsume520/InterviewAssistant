# 八爪猫

> <span class="icon">&#xe60f;</span> `(123)456-7890`&emsp;&emsp;
> <span class="icon">&#xe7ca;</span> `octocat@github.com`&emsp;&emsp;
> <span class="icon">&#xe600;</span> [octocat](https://github.com/octocat)

<img class="avatar" src="https://avatars.githubusercontent.com/u/583231?v=4">

## &#xe80c; 教育经历

<div class="entry-title">
    <h3>八爪科技大学 - 本科 - 软件工程专业</h3> 
    <p>2008.02 - 2024.06</p>
</div>

- 曾获奖项： GitHub 认证八爪编码员，敏捷章鱼实践者
- 校园经历： 担任 OctoStudio 队长，致力于推动服务于八爪生物的技术创新和项目开发。

## &#xe618; 工作经验

<div alt="entry-title">
    <h3>软件工程师 - 章小鱼有限公司</h3> 
    <p>2008.03 - 2009.07</p>
</div>

作为核心开发成员及技术负责人，主导了八爪生物社交平台（OctoHub）的全栈开发与架构设计。

- 设计并实现独特的"八爪风格"用户交互体系，包括：动态触手消息传递系统、墨水喷溅情感反应功能、自适应伪装个人主页，以促进全球八爪生物和猫之间的社区参与，使用户互动频率提升210%。
- 集成 OAuth 认证，与 GitHub 账户进行同步，为 Octocat 和其他在 GitHub 上活跃的八爪生物提供无缝登录和个人资料同步，将认证流程耗时从12.8s缩短至2.3s，获选GitHub年度最佳身份集成案例。

<div class="entry-title">
    <h3>软件开发实习生 - 八爪科技</h3> 
    <p>2008.06 - 2008.08</p>
</div>

与软件工程师团队合作，使用 Octolang 开发数据可视化仪表盘，为海洋保护工作提供八爪种群趋势的洞察。
- 参与会议和代码审议，按照敏捷章鱼论交付高质量的软件，在紧迫的截止日期内完成任务。
- 协助解决技术问题，展现解决问题的技巧和在快节奏环境下积极主动解决挑战的态度。为项目需求、架构设计和编码标准的文档撰写做出贡献，促进团队成员间的知识共享和新成员的快速适应。

## &#xe635; 项目经历

<div class="entry-title">
    <h3>GitFlix</h3>
    <a href="https://github.com/YiNNx/cmd-wrapped">github.com/octocat/gitflix</a>
</div>

全栈 Web 应用程序，前端使用 Octo.js，后端使用 OctoScript，允许用户发现和评价八爪生物主题电影。
- 实现了一个复杂的推荐算法，分析八爪生物的偏好和观影历史，为八爪生物跨多个流派提供八爪主题的电影推荐，确保了个性化和吸引人的内容发现。
- 使用 JSON Web Tokens 和 bcrypt 实现用户身份验证和授权，用于安全密码哈希。利用 GitHub Actions 进行持续集成和部署，确保流畅高效的开发工作流程。

<div class="entry-title">
    <h3>OctoGithubber</h3> 
    <a href="https://github.com/YiNNx/cmd-wrapped">github.com/octocat/gitflix</a>
</div>

* **技术栈：** Python, LangGraph, ChromaDB, FastAPI, SQLite, httpx, trafilatura

#### 【项目简介】
基于 LangGraph 编排的高级 RAG 与多智能体系统，支持本地双轨索引维护、置信度驱动的混合检索、具备人工介入（HITL）的简历优化子图与自适应深研子图。

#### 【核心功能与技术实现】
1. **双轨检索与分差比置信度判定（Gap Detection）**
   构建了向量数据库与本地语义索引（`index.md`）双轨结构。为解决短文本检索中相似度绝对分数坍缩问题，采用 `top_score / avg_scores`（分差比值 > 1.15）作为置信度判据，从而有效区分“确定的命中”与“没把握的噪声”。
2. **跨图层中断传播与多轮协同闭环（Interactive HITL Loop）**
   设计了子图 `interrupt()` 挂起异常在主图中的透传与捕获机制。结合 SQLite 持久化 Checkpoint，用户可在任意挂起步骤下通过 `Command` 恢复执行；提供单步快照回退能力，在最小状态字段下满足了用户撤销、批准与规划的协同需求。
3. **自适应重试的自主深研子图（Research & Auto-Indexing）**
   开发了 7 节点的深研子图，调用 DuckDuckGo 及正文提取工具爬取分析网页。在连通性校验节点中引入 POST-INTERRUPT 机制，用户恢复后可自动重试或进行重置，研讨结束后可自动唤醒后台 Index Agent 对新增文件进行索引重建。
## &#xecfa; 专业技能

- 熟练掌握多种编程语言，包括 Octolang，OctoScript 等，对面向对象和函数式编程范式有很好的理解，专注于编写清晰，高效，可维护的代码。
- 出色的沟通和语言能力，无论八爪生物抑或来自不同地区的猫，都能进行有效的团队合作和清晰技术概念沟通。
- 扎实的软件开发原理，数据结构和算法理解，熟悉计算机底层原理。
- 在版本控制方面有丰富的经验，熟练管理代码库、解决合并冲突，并促进代码审议。
