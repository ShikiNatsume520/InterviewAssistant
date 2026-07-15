import { useState } from "react";
import { KeyRound, X } from "lucide-react";
import type { ModelConfig } from "./types";

interface Props {
  initial: ModelConfig | null;
  onSave: (config: ModelConfig) => void;
  onClose: () => void;
}

export function ModelConfigModal({ initial, onSave, onClose }: Props) {
  const [baseUrl, setBaseUrl] = useState(initial?.baseUrl ?? "https://api.deepseek.com/v1");
  const [model, setModel] = useState(initial?.model ?? "deepseek-chat");
  const [apiKey, setApiKey] = useState(initial?.apiKey ?? "");

  return <div className="modal-backdrop" role="dialog" aria-modal="true">
    <form className="modal" onSubmit={(event) => { event.preventDefault(); onSave({ baseUrl: baseUrl.trim(), model: model.trim(), apiKey: apiKey.trim() }); }}>
      <button type="button" className="icon-button modal-close" onClick={onClose} aria-label="关闭"><X size={18} /></button>
      <div className="modal-icon"><KeyRound /></div>
      <h2>配置模型服务</h2>
      <p>配置只保存在当前浏览器会话中，每次执行通过请求头提交，不会写入后端数据库。</p>
      <label>Base URL<input required type="url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} /></label>
      <label>Model<input required value={model} onChange={(event) => setModel(event.target.value)} /></label>
      <label>API Key<input required type="password" autoComplete="off" value={apiKey} onChange={(event) => setApiKey(event.target.value)} /></label>
      <button className="primary-button" type="submit">保存并开始使用</button>
    </form>
  </div>;
}
