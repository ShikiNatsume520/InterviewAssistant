import { useState } from "react";
import { Bot, BrainCircuit, CheckCircle2, Circle, ExternalLink, FileText, Search, UserRound, Wrench, XCircle } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { AgentSource, ProductEvent } from "./types";
import type { PendingInterrupt } from "./interrupts";

export interface TransientMessage {
  messageId: string;
  source: AgentSource;
  text: string;
}

interface TimelineProps {
  events: ProductEvent[];
  transient: TransientMessage[];
  hasMore: boolean;
  loadingOlder: boolean;
  onLoadOlder: () => void;
  pendingInterrupt: PendingInterrupt | null;
  running: boolean;
  selection: string;
  onDecision: (value: Record<string, unknown>) => void;
}

const labels: Record<AgentSource, string> = {
  user: "你",
  main: "Main Agent",
  resume: "Resume Agent",
  research: "Research Agent",
  system: "系统",
};

function Avatar({ source }: { source: AgentSource }) {
  const Icon = source === "user" ? UserRound : source === "research" ? Search : source === "resume" ? BrainCircuit : Bot;
  return <span className={`avatar avatar-${source}`}><Icon size={17} /></span>;
}

function textPayload(event: ProductEvent): string {
  return typeof event.payload.text === "string" ? event.payload.text : "";
}

function toolDisplayName(raw: unknown): string {
  const name = String(raw ?? "tool");
  const labels: Record<string, string> = {
    rag_agent: "RAG Agent",
    research_agent: "Research Agent",
    resume_agent: "Resume Agent",
    list_resumes: "简历列表工具",
    search_resumes: "简历搜索工具",
    read_resume: "简历读取工具",
    create_new_resume: "简历创建工具",
  };
  return labels[name] ?? name;
}

function InterruptCard({ interrupt, running, selection, onDecision }: { interrupt: PendingInterrupt; running: boolean; selection: string; onDecision: (value: Record<string, unknown>) => void }) {
  const [suggestion, setSuggestion] = useState("");
  const data = interrupt.data;
  if (interrupt.phase === "plan_confirm") {
    const plan = Array.isArray(data.plan) ? data.plan : [];
    return <div className="interrupt-card"><strong>确认修改计划</strong><ol>{plan.map((item, index) => <li key={index}>{String(item)}</li>)}</ol><textarea value={suggestion} onChange={(event) => setSuggestion(event.target.value)} placeholder="如需调整，请填写建议"/><div className="interrupt-actions"><button disabled={running} className="primary" onClick={() => onDecision({ action: "approve" })}>批准计划</button><button disabled={running || !suggestion.trim()} onClick={() => onDecision({ action: "suggest", suggestion: suggestion.trim(), selection })}>调整计划</button></div></div>;
  }
  if (interrupt.phase === "resume_approve") {
    return <div className="interrupt-card"><strong>修改 {String(data.ordinal ?? 1)} / {String(data.total ?? 1)} · {String(data.section || "简历内容")}</strong><p>{String(data.reason || "优化当前内容表达")}</p><textarea value={suggestion} onChange={(event) => setSuggestion(event.target.value)} placeholder="输入你希望如何调整"/><div className="interrupt-actions"><button disabled={running} className="primary" onClick={() => onDecision({ action: "approve" })}>接受修改</button><button disabled={running} onClick={() => onDecision({ action: "reject" })}>拒绝</button><button disabled={running || !suggestion.trim()} onClick={() => onDecision({ action: "suggest", suggestion: suggestion.trim(), selection })}>提出建议</button></div></div>;
  }
  if (interrupt.phase === "outline_confirm") {
    const outline = Array.isArray(data.outline) ? data.outline : [];
    return <div className="interrupt-card research-interrupt"><strong>确认深度研究计划</strong><p>研究主题：{String(data.gap_topic ?? "")}</p><ol>{outline.map((item, index) => <li key={index}>{String(item)}</li>)}</ol><textarea value={suggestion} onChange={(event) => setSuggestion(event.target.value)} placeholder="如需调整检索方向，请填写建议"/><div className="interrupt-actions"><button disabled={running} className="primary" onClick={() => onDecision({ action: "approve" })}>批准并开始研究</button><button disabled={running || !suggestion.trim()} onClick={() => onDecision({ action: "suggest", suggestion: suggestion.trim() })}>调整计划</button><button disabled={running} className="danger" onClick={() => onDecision({ action: "reject" })}>取消研究</button></div></div>;
  }
  if (interrupt.phase === "connectivity_check") {
    return <div className="interrupt-card research-interrupt"><strong>联网检查失败</strong><p>{String(data.msg ?? "当前无法连接搜索服务，请检查网络后继续。")}</p><div className="interrupt-actions"><button disabled={running} className="primary" onClick={() => onDecision({ action: "continue" })}>重新检查</button></div></div>;
  }
  if (interrupt.phase === "research_knowledge_confirm") {
    return <div className="interrupt-card research-interrupt"><strong>是否加入个人知识库？</strong><p>{String(data.summary ?? "研究报告已经生成。")}</p><dl className="research-confirm-meta"><div><dt>有效来源</dt><dd>{String(data.source_count ?? 0)}</dd></div><div><dt>建议文件名</dt><dd>{String(data.proposed_file_name ?? "")}</dd></div></dl><p className="standby-guidance">同意后将记录入库授权；当前导入状态会显示为待处理。</p><div className="interrupt-actions"><button disabled={running} className="primary" onClick={() => onDecision({ action: "approve" })}>同意加入</button><button disabled={running} onClick={() => onDecision({ action: "reject" })}>暂不加入</button></div></div>;
  }
  if (interrupt.phase === "resume_hitl") {
    return <div className="interrupt-card standby-card"><strong>Resume Agent正在待命</strong><p>{String(data.summary || "本轮修改已经完成。")}</p><p className="standby-guidance">你可以直接在下方聊天框继续提出修改要求；只有准备结束本次简历修改时，才需要选择以下退出操作。</p><div className="interrupt-actions"><button disabled={running} className="primary" onClick={() => onDecision({ action: "exit", save: true })}>保存为新简历并退出</button><button disabled={running} className="danger" onClick={() => onDecision({ action: "exit", save: false })}>不保存并退出</button></div></div>;
  }
  return <div className="interrupt-card"><strong>等待用户确认</strong><p>当前任务已暂停，请根据提示继续。</p></div>;
}

const researchLabels: Record<string, string> = {
  planning: "正在制定研究计划",
  waiting_plan: "等待计划确认",
  checking_network: "正在检查网络",
  searching: "正在搜索网页",
  fetching: "正在抓取网页",
  distilling: "正在提炼资料",
  composing_report: "正在生成报告",
  waiting_knowledge_decision: "等待知识库确认",
  completed: "研究已完成",
  aborted: "研究已终止",
};

function ResearchCard({ taskEvents }: { taskEvents: ProductEvent[] }) {
  let phase = "planning";
  let keywords: string[] = [];
  let report = "";
  let summary = "";
  let decision = "";
  let queriesCompleted = 0;
  let queriesTotal = 0;
  const sources = new Map<string, Record<string, unknown>>();
  for (const event of taskEvents) {
    if (event.type === "research.plan" && Array.isArray(event.payload.keywords)) keywords = event.payload.keywords.map(String);
    if (event.type === "research.progress" && typeof event.payload.phase === "string") {
      phase = event.payload.phase;
      if (typeof event.payload.queriesCompleted === "number" && event.payload.queriesCompleted > 0) queriesCompleted = event.payload.queriesCompleted;
      if (typeof event.payload.queriesTotal === "number" && event.payload.queriesTotal > 0) queriesTotal = event.payload.queriesTotal;
    }
    if (event.type === "research.source" && typeof event.payload.sourceId === "string") sources.set(event.payload.sourceId, event.payload);
    if (event.type === "research.report") {
      report = typeof event.payload.markdown === "string" ? event.payload.markdown : "";
      summary = typeof event.payload.summary === "string" ? event.payload.summary : "";
    }
    if (event.type === "research.knowledge-decision") decision = String(event.payload.decision ?? "");
  }
  const sourceList = [...sources.values()];
  const completed = sourceList.filter((source) => ["succeeded", "failed", "skipped"].includes(String(source.status))).length;
  return <section className="research-card">
    <header><div><Search size={17}/><strong>深度研究</strong></div><span>{researchLabels[phase] ?? phase}</span></header>
    {keywords.length > 0 && <details open={phase === "waiting_plan"}><summary>研究关键词 {queriesTotal > 0 ? `${queriesCompleted}/${queriesTotal}` : keywords.length}</summary><ol>{keywords.map((keyword, index) => <li className={index < queriesCompleted ? "completed" : ""} key={keyword}>{keyword}</li>)}</ol></details>}
    {sourceList.length > 0 && <details open={!report}><summary>网页来源 {completed}/{sourceList.length}</summary><div className="research-sources">{sourceList.map((source) => {
      const status = String(source.status ?? "pending");
      const StatusIcon = status === "succeeded" ? CheckCircle2 : status === "failed" ? XCircle : Circle;
      return <div className={`research-source status-${status}`} key={String(source.sourceId)}><StatusIcon size={14}/><div><a href={String(source.url)} target="_blank" rel="noreferrer">{String(source.title || source.url)}<ExternalLink size={11}/></a><small>{String(source.query ?? "")} · {status}{source.failureReason ? ` · ${String(source.failureReason)}` : ""}</small></div></div>;
    })}</div></details>}
    {report && <details className="research-report" open><summary>研究报告</summary><p className="research-summary">{summary}</p><ReactMarkdown remarkPlugins={[remarkGfm]}>{report}</ReactMarkdown></details>}
    {decision && <div className="research-decision">{decision === "approved" ? "已记录个人知识库入库授权 · 等待导入" : "已选择不加入个人知识库"}</div>}
  </section>;
}

export function Timeline({ events, transient, hasMore, loadingOlder, onLoadOlder, pendingInterrupt, running, selection, onDecision }: TimelineProps) {
  const taskStatus = new Map<string, string>();
  for (const event of events) {
    if (event.taskId && event.type.startsWith("task.")) {
      const label = event.payload.label;
      if (typeof label === "string") taskStatus.set(`${event.taskId}:${event.source}`, label);
    }
  }
  const researchEventTypes = new Set(["research.plan", "research.progress", "research.source", "research.report", "research.knowledge-decision"]);
  const renderedResearchTasks = new Set<string>();

  return (
    <div className="timeline" aria-live="polite">
      {hasMore && <button className="load-older" disabled={loadingOlder} onClick={onLoadOlder}>{loadingOlder ? "加载中…" : "加载更早消息"}</button>}
      {events.length === 0 && transient.length === 0 && (
        <div className="empty-chat"><BrainCircuit size={30} /><h2>开始新的对话</h2><p>可以询问面试准备、知识库资料或简历优化。</p></div>
      )}
      {events.map((event) => {
        if (researchEventTypes.has(event.type)) {
          const key = event.taskId ?? event.eventId;
          if (renderedResearchTasks.has(key)) return null;
          renderedResearchTasks.add(key);
          return <ResearchCard key={`research:${key}`} taskEvents={events.filter((candidate) => candidate.taskId === event.taskId && researchEventTypes.has(candidate.type))}/>;
        }
        if (event.type === "agent.transition") {
          return <div className="transition" key={event.eventId}>{labels[event.payload.from as AgentSource] ?? "Agent"} 已将任务交给 {labels[event.payload.to as AgentSource] ?? "Agent"}</div>;
        }
        if (event.type === "tool.status") {
          const toolName = toolDisplayName(event.payload.tool);
          return <details className="tool-card" key={event.eventId}><summary><Wrench size={14} />{toolName} 执行完成</summary><div>工具：{toolName}</div></details>;
        }
        if (event.type === "citation.list") {
          const items = Array.isArray(event.payload.items) ? event.payload.items : [];
          return <details className="tool-card citation-card" key={event.eventId}><summary><Search size={14} />参考资料 {items.length}</summary><ol>{items.map((item, index) => {
            if (typeof item !== "object" || item === null) return <li key={index}>{String(item)}</li>;
            const citation = item as Record<string, unknown>;
            return <li key={index}><strong>{String(citation.file_path ?? "知识库资料")}</strong><span>第 {String(citation.start_line ?? "?")}–{String(citation.end_line ?? "?")} 行</span><p>{String(citation.content ?? "")}</p></li>;
          })}</ol></details>;
        }
        if (event.type === "resume.result") {
          const saved = event.payload.outcome === "saved" && typeof event.payload.output_resume_id === "string";
          return <div className="resume-result-card" key={event.eventId}><FileText size={17}/><div><strong>{saved ? "新简历已保存" : "已放弃本轮草稿"}</strong><span>{String(event.payload.summary ?? "")}</span></div>{saved && <a href={`/v1/resumes/${String(event.payload.output_resume_id)}/download`}>下载 Markdown</a>}</div>;
        }
        if (event.type === "interrupt.requested") {
          return event.eventId === pendingInterrupt?.eventId
            ? <InterruptCard key={event.eventId} interrupt={pendingInterrupt} running={running} selection={selection} onDecision={onDecision} />
            : <details className="tool-card" key={event.eventId}><summary>历史审批记录</summary><div>该决策已处理，不可再次操作。</div></details>;
        }
        if (event.type === "task.failed" || event.type === "task.interrupted") {
          return <div className="notice-card" key={event.eventId}>{String(event.payload.label ?? "任务已中断")}</div>;
        }
        if (event.type !== "message.user" && event.type !== "message.agent") return null;
        const status = event.taskId ? taskStatus.get(`${event.taskId}:${event.source}`) : undefined;
        const resumeName = typeof event.payload.resumeDisplayName === "string" ? event.payload.resumeDisplayName : null;
        return (
          <article className={`message-row ${event.source === "user" ? "from-user" : ""}`} key={event.eventId}>
            <Avatar source={event.source} />
            <div className="message-column">
              <div className="message-meta">{labels[event.source]}{status && <span className="agent-status"><CheckCircle2 size={11} />{status}</span>}</div>
              <div className="message-bubble">{resumeName && <div className="message-resume-attachment"><FileText size={12} />{resumeName}</div>}<ReactMarkdown remarkPlugins={[remarkGfm]}>{textPayload(event)}</ReactMarkdown></div>
            </div>
          </article>
        );
      })}
      {transient.map((message) => (
        <article className="message-row" key={message.messageId}>
          <Avatar source={message.source} />
          <div className="message-column">
            <div className="message-meta">{labels[message.source]}<span className="agent-status running">执行中</span></div>
            <div className="message-bubble streaming"><ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown></div>
          </div>
        </article>
      ))}
    </div>
  );
}
