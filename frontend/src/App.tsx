import { useCallback, useEffect, useRef, useState } from "react";
import { Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { KeyRound, LogOut, MessageSquarePlus, MoreHorizontal, Send, Settings2, Square, Trash2 } from "lucide-react";
import {
  createGuestIdentity,
  createThread,
  deleteThread,
  developerLogin,
  getCapabilities,
  getEvents,
  getIdentity,
  listThreads,
  renameThread,
  restoreGuest,
  streamGraph,
} from "./api";
import { loadModelConfig, saveModelConfig } from "./modelConfig";
import { ModelConfigModal } from "./ModelConfigModal";
import { Timeline, type TransientMessage } from "./Timeline";
import { ApiError, type Identity, type ModelConfig, type ProductEvent, type StreamFrame, type ThreadRecord } from "./types";

function mergeEvents(current: ProductEvent[], incoming: ProductEvent[]): ProductEvent[] {
  const byId = new Map(current.map((event) => [event.eventId, event]));
  for (const event of incoming) byId.set(event.eventId, event);
  return [...byId.values()].sort((left, right) => left.sequence - right.sequence);
}

export function App() {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [threads, setThreads] = useState<ThreadRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [developerEnabled, setDeveloperEnabled] = useState(false);
  const [modelConfig, setModelConfig] = useState<ModelConfig | null>(() => loadModelConfig());
  const [showModelConfig, setShowModelConfig] = useState(false);
  const navigate = useNavigate();

  const refreshThreads = useCallback(async () => setThreads(await listThreads()), []);

  useEffect(() => {
    void (async () => {
      try {
        let current: Identity;
        try { current = await getIdentity(); } catch { current = await createGuestIdentity(); }
        setIdentity(current);
        const [items, capabilities] = await Promise.all([listThreads(), getCapabilities()]);
        setThreads(items);
        setDeveloperEnabled(capabilities.developerLogin);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const handleCreate = async () => {
    const created = await createThread();
    await refreshThreads();
    navigate(`/threads/${created.id}`);
  };

  const handleDeveloperLogin = async () => {
    const token = window.prompt("请输入本地开发访问凭证");
    if (!token) return;
    try {
      setIdentity(await developerLogin(token));
      await refreshThreads();
      navigate("/");
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "开发人员登录失败");
    }
  };

  const handleRestoreGuest = async () => {
    setIdentity(await restoreGuest());
    await refreshThreads();
    navigate("/");
  };

  if (loading) return <div className="boot-screen"><div className="boot-mark">IA</div><span>正在恢复会话…</span></div>;
  if (!identity) return <div className="boot-screen">无法建立游客会话，请刷新重试。</div>;

  return <>
    <Routes>
      <Route path="/" element={threads[0] ? <Navigate replace to={`/threads/${threads[0].id}`} /> : <Welcome onCreate={handleCreate} />} />
      <Route path="/threads/:threadId" element={<ChatPage identity={identity} threads={threads} modelConfig={modelConfig} onThreadsChange={refreshThreads} onCreate={handleCreate} onOpenModel={() => { if (identity.kind === "guest") setShowModelConfig(true); }} developerEnabled={developerEnabled} onDeveloperLogin={handleDeveloperLogin} onRestoreGuest={handleRestoreGuest} />} />
      <Route path="*" element={<Navigate replace to="/" />} />
    </Routes>
    {showModelConfig && <ModelConfigModal initial={modelConfig} onClose={() => setShowModelConfig(false)} onSave={(config) => { saveModelConfig(config); setModelConfig(config); setShowModelConfig(false); }} />}
  </>;
}

function Welcome({ onCreate }: { onCreate: () => void }) {
  return <div className="welcome"><div className="welcome-mark">IA</div><h1>Interview Assistant</h1><p>创建一个会话，开始你的面试准备。</p><button className="primary-button" onClick={onCreate}><MessageSquarePlus size={17} />新建会话</button></div>;
}

interface ChatPageProps {
  identity: Identity;
  threads: ThreadRecord[];
  modelConfig: ModelConfig | null;
  developerEnabled: boolean;
  onThreadsChange: () => Promise<void>;
  onCreate: () => Promise<void>;
  onOpenModel: () => void;
  onDeveloperLogin: () => Promise<void>;
  onRestoreGuest: () => Promise<void>;
}

function ChatPage(props: ChatPageProps) {
  const { threadId = "" } = useParams();
  const navigate = useNavigate();
  const [events, setEvents] = useState<ProductEvent[]>([]);
  const [transient, setTransient] = useState<Map<string, TransientMessage>>(new Map());
  const [hasMore, setHasMore] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [message, setMessage] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [stoppedNotice, setStoppedNotice] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const current = props.threads.find((thread) => thread.id === threadId);

  useEffect(() => {
    abortRef.current?.abort();
    setEvents([]);
    setTransient(new Map());
    setError(null);
    setStoppedNotice(false);
    if (!threadId) return;
    void getEvents(threadId).then((page) => { setEvents(page.events); setHasMore(page.hasMore); }).catch((reason) => setError(reason instanceof ApiError ? reason : new ApiError("时间线加载失败", "EVENTS_FAILED", 500, true)));
  }, [threadId]);

  const onFrame = useCallback((frame: StreamFrame) => {
    if (frame.kind === "event") {
      setEvents((currentEvents) => mergeEvents(currentEvents, [frame.event]));
      if (frame.event.type === "message.agent" && typeof frame.event.payload.messageId === "string") {
        setTransient((currentTransient) => { const next = new Map(currentTransient); next.delete(frame.event.payload.messageId as string); return next; });
      }
      return;
    }
    setTransient((currentTransient) => {
      const next = new Map(currentTransient);
      const previous = next.get(frame.messageId);
      next.set(frame.messageId, { messageId: frame.messageId, source: frame.source, text: (previous?.text ?? "") + frame.text });
      return next;
    });
  }, []);

  const run = async (checkpoint = false) => {
    if (!threadId || running) return;
    if (props.identity.kind === "guest" && !props.modelConfig) { props.onOpenModel(); return; }
    const controller = new AbortController();
    abortRef.current = controller;
    setRunning(true);
    setError(null);
    setStoppedNotice(false);
    const outgoingMessage = message;
    if (!checkpoint) setMessage("");
    try {
      await streamGraph({ threadId, message: outgoingMessage, checkpoint, modelConfig: props.modelConfig, signal: controller.signal, onFrame });
      await props.onThreadsChange();
    } catch (reason) {
      if ((reason as DOMException)?.name === "AbortError") {
        setStoppedNotice(true);
        await props.onThreadsChange();
      } else {
        if (!checkpoint) setMessage(outgoingMessage);
        setError(reason instanceof ApiError ? reason : new ApiError("连接意外中断", "STREAM_FAILED", 500, true));
      }
    } finally {
      setTransient(new Map());
      setRunning(false);
      abortRef.current = null;
    }
  };

  const loadOlder = async () => {
    const first = events[0];
    if (!first) return;
    setLoadingOlder(true);
    try { const page = await getEvents(threadId, { before: first.eventId }); setEvents((currentEvents) => mergeEvents(page.events, currentEvents)); setHasMore(page.hasMore); } finally { setLoadingOlder(false); }
  };

  const rename = async (thread: ThreadRecord) => {
    const title = window.prompt("新的会话名称", thread.title)?.trim();
    if (!title || title === thread.title) return;
    await renameThread(thread.id, title);
    await props.onThreadsChange();
  };

  const remove = async (thread: ThreadRecord) => {
    if (!window.confirm(`删除会话“${thread.title}”？此操作不可撤销。`)) return;
    await deleteThread(thread.id);
    await props.onThreadsChange();
    navigate("/");
  };

  if (!current) return <Navigate replace to="/" />;
  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark">IA</div><span>Interview Assistant</span></div>
      <button className="new-thread" onClick={() => void props.onCreate()}><MessageSquarePlus size={17} />新建会话</button>
      <div className="sidebar-label">最近会话</div>
      <nav className="thread-list">{props.threads.map((thread) => <div className={`thread-item ${thread.id === threadId ? "active" : ""}`} key={thread.id}><button className="thread-link" onClick={() => navigate(`/threads/${thread.id}`)}><span>{thread.title}</span><small>{thread.status === "running" ? "执行中" : thread.status === "interrupted" ? "已中断" : "就绪"}</small></button><div className="thread-actions"><button title="重命名" onClick={() => void rename(thread)}><MoreHorizontal size={15} /></button><button title="删除" onClick={() => void remove(thread)}><Trash2 size={14} /></button></div></div>)}</nav>
      <div className="sidebar-footer">
        <button onClick={props.onOpenModel}><Settings2 size={16} />{props.identity.kind === "developer" ? "开发环境配置" : "模型配置"}</button>
        {props.identity.kind === "developer" ? <button onClick={() => void props.onRestoreGuest()}><LogOut size={16} />返回游客模式</button> : props.developerEnabled && <button onClick={() => void props.onDeveloperLogin()}><KeyRound size={16} />开发人员登录</button>}
      </div>
    </aside>
    <main className="chat-panel">
      <header className="chat-header"><div><h1>{current.title}</h1><span>{props.identity.kind === "developer" ? "开发人员模式 · 使用服务端配置" : "游客模式 · 使用当前会话模型配置"}</span></div></header>
      <Timeline events={events} transient={[...transient.values()]} hasMore={hasMore} loadingOlder={loadingOlder} onLoadOlder={() => void loadOlder()} />
      {error && <div className="error-bar"><span>{error.message}</span>{error.retryable && <button onClick={() => void run(true)}>从检查点重试</button>}</div>}
      {stoppedNotice && <div className="resume-bar">已停止显示；后端将在当前节点结束后保存中断检查点。</div>}
      {current.status === "interrupted" && !running && <div className="resume-bar">上次执行已中断。<button onClick={() => void run(true)}>从最近检查点继续</button></div>}
      <div className="composer"><textarea value={message} onChange={(event) => setMessage(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); if (message.trim()) void run(false); } }} placeholder="输入消息，Enter 发送，Shift + Enter 换行" disabled={running} /><button aria-label={running ? "停止生成" : "发送消息"} className={running ? "stop-button" : "send-button"} disabled={!running && !message.trim()} onClick={() => running ? abortRef.current?.abort() : void run(false)}>{running ? <Square size={17} fill="currentColor" /> : <Send size={18} />}</button></div>
    </main>
  </div>;
}
