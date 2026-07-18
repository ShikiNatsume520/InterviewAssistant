import { useEffect, useRef, useState } from "react";
import { BookOpen, FileText, RefreshCw, Trash2, Upload } from "lucide-react";
import {
  deleteKnowledge,
  listKnowledgeResources,
  readKnowledgeResource,
  reindexKnowledge,
  uploadKnowledge,
} from "./api";
import type { IdentityKind, KnowledgeDocument, KnowledgeResource, ModelConfig } from "./types";

interface Props {
  modelConfig: ModelConfig | null;
  requireModelConfig: () => boolean;
  identityKind: IdentityKind;
}

export function KnowledgePage({ modelConfig, requireModelConfig, identityKind }: Props) {
  const [items, setItems] = useState<KnowledgeResource[]>([]);
  const [selected, setSelected] = useState<KnowledgeDocument | null>(null);
  const [filter, setFilter] = useState<"all" | "personal" | "public" | "research" | "failed">("all");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [uploadScope, setUploadScope] = useState<"personal" | "public">(identityKind === "developer" ? "public" : "personal");
  const inputRef = useRef<HTMLInputElement | null>(null);

  const refresh = async () => setItems(await listKnowledgeResources());
  useEffect(() => { void refresh().catch((reason) => setError(String(reason))); }, []);

  const visible = items.filter((item) => {
    if (filter === "all") return true;
    if (filter === "failed") return item.status === "failed" || item.status === "delete_failed";
    if (filter === "research") return item.sourceType === "research";
    return item.scope === filter;
  });

  const upload = async (file: File) => {
    if (!file.name.toLowerCase().endsWith(".md")) { setError("只支持 Markdown 文件"); return; }
    if (!requireModelConfig()) return;
    setBusy("upload"); setError("");
    try { await uploadKnowledge(file, modelConfig, uploadScope); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "上传失败"); }
    finally { setBusy(null); }
  };

  const open = async (item: KnowledgeResource) => {
    setBusy(item.id); setError("");
    try { setSelected(await readKnowledgeResource(item.id)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "读取失败"); }
    finally { setBusy(null); }
  };

  const reindex = async (item: KnowledgeResource) => {
    if (!requireModelConfig()) return;
    setBusy(item.id); setError("");
    try { await reindexKnowledge(item.id, modelConfig); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "重新索引失败"); }
    finally { setBusy(null); }
  };

  const remove = async (item: KnowledgeResource) => {
    const impact = item.scope === "public" ? "这会影响所有用户的 RAG 检索。" : "";
    if (!window.confirm(`永久删除“${item.displayName}”？Markdown、关键词索引和向量都会被清除，且不可撤销。${impact}`)) return;
    setBusy(item.id); setError("");
    try { await deleteKnowledge(item.id); if (selected?.id === item.id) setSelected(null); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "删除失败"); }
    finally { setBusy(null); }
  };

  return <main className="knowledge-panel" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); const file = event.dataTransfer.files[0]; if (file) void upload(file); }}>
    <header className="knowledge-header"><div><h1>知识库</h1><p>{identityKind === "developer" ? "开发人员可管理公共知识；个人资料仍按身份隔离。" : "公共资料只读；个人资料仅对当前身份可见。"}</p></div>{identityKind === "developer" && <select className="knowledge-scope-select" value={uploadScope} onChange={(event) => setUploadScope(event.target.value as "personal" | "public")}><option value="public">上传到公共知识</option><option value="personal">上传到个人知识</option></select>}<button className="primary-button" disabled={busy !== null} onClick={() => inputRef.current?.click()}><Upload size={16} />{busy === "upload" ? "正在索引…" : "上传 Markdown"}</button><input ref={inputRef} hidden type="file" accept=".md,text/markdown" onChange={(event) => { const file = event.target.files?.[0]; if (file) void upload(file); event.target.value = ""; }} /></header>
    <div className="knowledge-filters">{([['all','全部'],['personal','个人'],['public','公共'],['research','深研资料'],['failed','索引失败']] as const).map(([value,label]) => <button className={filter === value ? "active" : ""} key={value} onClick={() => setFilter(value)}>{label}</button>)}</div>
    {error && <div className="knowledge-error">{error}</div>}
    <div className="knowledge-body">
      <section className="knowledge-list">{visible.length === 0 ? <div className="knowledge-empty"><BookOpen size={32} /><span>暂无符合条件的知识资料</span><small>可将 Markdown 拖到此页面上传</small></div> : visible.map((item) => <article className={`knowledge-item ${selected?.id === item.id ? "active" : ""}`} key={item.id} onClick={() => void open(item)}><FileText size={18} /><div><strong>{item.displayName}</strong><span>{item.scope === "public" ? "公共资料" : item.sourceType === "research" ? "深研资料" : "个人上传"} · {item.status === "ready" ? "索引就绪" : item.status}</span>{item.failureReason && <small>{item.failureReason}</small>}</div>{!item.readOnly && <div className="knowledge-actions"><button title="重新索引" disabled={busy !== null} onClick={(event) => { event.stopPropagation(); void reindex(item); }}><RefreshCw size={15} /></button><button title="删除" disabled={busy !== null} onClick={(event) => { event.stopPropagation(); void remove(item); }}><Trash2 size={15} /></button></div>}</article>)}</section>
      <section className="knowledge-preview">{selected ? <><header><h2>{selected.displayName}</h2><span>{selected.readOnly ? "公共 · 只读" : "个人 · 只读预览"}</span></header><pre>{selected.content}</pre></> : <div className="knowledge-empty">选择资料查看 Markdown 原文</div>}</section>
    </div>
  </main>;
}
