import { Download, FileText, Pencil, Trash2, UploadCloud, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { deleteResume, downloadResume, listResumes, renameResume, uploadResume } from "./api";
import { ApiError, type ResumeMetadata } from "./types";

const MAX_BYTES = 1024 * 1024;

interface ResumePickerProps {
  selectedId: string | null;
  onSelect: (resume: ResumeMetadata) => Promise<void>;
  onClear: () => Promise<void>;
  onClose: () => void;
}

export function ResumePicker({ selectedId, onSelect, onClear, onClose }: ResumePickerProps) {
  const [items, setItems] = useState<ResumeMetadata[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const refresh = async () => setItems(await listResumes());
  useEffect(() => { void refresh().catch((reason) => setError(messageOf(reason))); }, []);

  const acceptFile = async (file: File) => {
    if (!file.name.toLowerCase().endsWith(".md")) { setError("只支持单个 .md 文件"); return; }
    if (file.size > MAX_BYTES) { setError("Markdown 文件不能超过 1 MiB"); return; }
    setBusy(true); setError("");
    try {
      const created = await uploadResume(file);
      await refresh();
      await onSelect(created);
    } catch (reason) { setError(messageOf(reason)); } finally { setBusy(false); }
  };

  const rename = async (item: ResumeMetadata) => {
    const name = window.prompt("新的简历显示名称", item.display_name)?.trim();
    if (!name || name === item.display_name) return;
    try { await renameResume(item.id, name); await refresh(); } catch (reason) { setError(messageOf(reason)); }
  };

  const remove = async (item: ResumeMetadata) => {
    if (!window.confirm(`永久删除简历“${item.display_name}”？此操作不可撤销。`)) return;
    try {
      await deleteResume(item.id);
      if (selectedId === item.id) await onClear();
      await refresh();
    } catch (reason) { setError(messageOf(reason)); }
  };

  return <div className="resume-picker" role="dialog" aria-label="指定简历">
    <div className="resume-picker-head"><strong>指定简历</strong><button className="icon-button" onClick={onClose}><X size={16} /></button></div>
    <button className="resume-drop" disabled={busy} onClick={() => inputRef.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); const files = [...event.dataTransfer.files]; if (files.length !== 1) { setError("每次只能上传一个 Markdown 文件"); return; } void acceptFile(files[0]); }}>
      <UploadCloud size={22} /><strong>{busy ? "正在上传…" : "拖入 Markdown 简历"}</strong><span>或点击选择单个 .md 文件</span>
    </button>
    <input ref={inputRef} hidden type="file" accept=".md,text/markdown" onChange={(event) => { const file = event.target.files?.[0]; if (file) void acceptFile(file); event.currentTarget.value = ""; }} />
    {error && <div className="resume-picker-error">{error}</div>}
    <div className="resume-picker-list">
      {items.length === 0 && !busy && <div className="resume-picker-empty">尚未上传简历</div>}
      {items.map((item) => <div className={`resume-picker-item ${item.id === selectedId ? "selected" : ""}`} key={item.id}>
        <button className="resume-picker-select" onClick={() => void onSelect(item)}><FileText size={17} /><span><strong>{item.display_name}</strong><small>{item.original_name} · {new Date(item.updated_at).toLocaleDateString()}</small></span></button>
        <div className="resume-picker-actions"><button title="重命名" onClick={() => void rename(item)}><Pencil size={13} /></button><button title="下载" onClick={() => void downloadResume(item.id, item.display_name).catch((reason) => setError(messageOf(reason)))}><Download size={13} /></button><button title="删除" onClick={() => void remove(item)}><Trash2 size={13} /></button></div>
      </div>)}
    </div>
  </div>;
}

function messageOf(reason: unknown): string {
  return reason instanceof ApiError || reason instanceof Error ? reason.message : "操作失败";
}
