import { Bot, BrainCircuit, CheckCircle2, Search, UserRound, Wrench } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { AgentSource, ProductEvent } from "./types";

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

export function Timeline({ events, transient, hasMore, loadingOlder, onLoadOlder }: TimelineProps) {
  const taskStatus = new Map<string, string>();
  for (const event of events) {
    if (event.taskId && event.type.startsWith("task.")) {
      const label = event.payload.label;
      if (typeof label === "string") taskStatus.set(`${event.taskId}:${event.source}`, label);
    }
  }

  return (
    <div className="timeline" aria-live="polite">
      {hasMore && <button className="load-older" disabled={loadingOlder} onClick={onLoadOlder}>{loadingOlder ? "加载中…" : "加载更早消息"}</button>}
      {events.length === 0 && transient.length === 0 && (
        <div className="empty-chat"><BrainCircuit size={30} /><h2>开始新的对话</h2><p>可以询问面试准备、知识库资料或简历优化。</p></div>
      )}
      {events.map((event) => {
        if (event.type === "agent.transition") {
          return <div className="transition" key={event.eventId}>{labels[event.payload.from as AgentSource] ?? "Agent"} 已将任务交给 {labels[event.payload.to as AgentSource] ?? "Agent"}</div>;
        }
        if (event.type === "tool.status") {
          return <details className="tool-card" key={event.eventId}><summary><Wrench size={14} /> 工具执行完成</summary><div>{String(event.payload.tool ?? "tool")}</div></details>;
        }
        if (event.type === "citation.list") {
          const items = Array.isArray(event.payload.items) ? event.payload.items : [];
          return <details className="tool-card citation-card" key={event.eventId}><summary><Search size={14} />参考资料 {items.length}</summary><ol>{items.map((item, index) => {
            if (typeof item !== "object" || item === null) return <li key={index}>{String(item)}</li>;
            const citation = item as Record<string, unknown>;
            return <li key={index}><strong>{String(citation.file_path ?? "知识库资料")}</strong><span>第 {String(citation.start_line ?? "?")}–{String(citation.end_line ?? "?")} 行</span><p>{String(citation.content ?? "")}</p></li>;
          })}</ol></details>;
        }
        if (event.type === "interrupt.requested") {
          return <div className="notice-card" key={event.eventId}>当前任务正在等待确认。交互审批将在后续对应功能阶段接入。</div>;
        }
        if (event.type === "task.failed" || event.type === "task.interrupted") {
          return <div className="notice-card" key={event.eventId}>{String(event.payload.label ?? "任务已中断")}</div>;
        }
        if (event.type !== "message.user" && event.type !== "message.agent") return null;
        const status = event.taskId ? taskStatus.get(`${event.taskId}:${event.source}`) : undefined;
        return (
          <article className={`message-row ${event.source === "user" ? "from-user" : ""}`} key={event.eventId}>
            <Avatar source={event.source} />
            <div className="message-column">
              <div className="message-meta">{labels[event.source]}{status && <span className="agent-status"><CheckCircle2 size={11} />{status}</span>}</div>
              <div className="message-bubble"><ReactMarkdown remarkPlugins={[remarkGfm]}>{textPayload(event)}</ReactMarkdown></div>
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
