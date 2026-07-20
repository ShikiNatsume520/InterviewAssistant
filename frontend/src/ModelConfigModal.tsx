import { useState } from "react";
import { CheckCircle2, KeyRound, Trash2, X } from "lucide-react";
import { testModelConfig } from "./api";
import type { IdentityKind, ModelConfig } from "./types";

interface Props {
  initial: ModelConfig | null;
  identityKind: IdentityKind;
  onSave: (config: ModelConfig) => void;
  onClear: () => void;
  onClose: () => void;
}

const PRESETS = {
  deepseek: { baseUrl: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  openai: { baseUrl: "https://api.openai.com/v1", model: "gpt-4o-mini" },
  custom: { baseUrl: "", model: "" },
} as const;

export function ModelConfigModal({ initial, identityKind, onSave, onClear, onClose }: Props) {
  const [providerId, setProviderId] = useState<ModelConfig["providerId"]>(initial?.providerId ?? "deepseek");
  const [baseUrl, setBaseUrl] = useState(initial?.baseUrl ?? PRESETS.deepseek.baseUrl);
  const [model, setModel] = useState(initial?.model ?? PRESETS.deepseek.model);
  const [apiKey, setApiKey] = useState(initial?.apiKey ?? "");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);

  if (identityKind === "developer") {
    return <div className="modal-backdrop" role="dialog" aria-modal="true"><div className="modal"><button type="button" className="icon-button modal-close" onClick={onClose} aria-label="关闭"><X size={18} /></button><div className="modal-icon"><KeyRound /></div><h2>开发环境模型配置</h2><div className="developer-model-status"><CheckCircle2 size={19} /><div><strong>凭据来源：服务端 .env</strong><p>模型地址、模型名和 API Key 由开发环境管理，不会发送或展示到浏览器。</p></div></div><button type="button" className="primary-button" disabled={testing} onClick={() => { setTesting(true); setTestResult(null); void testModelConfig(null).then((result) => setTestResult({ ok: true, message: `连接成功 · ${result.model}` })).catch(() => setTestResult({ ok: false, message: "连接失败，请检查服务端 .env" })).finally(() => setTesting(false)); }}>{testing ? "正在测试…" : "测试服务端连接"}</button>{testResult && <p className={testResult.ok ? "model-test-success" : "model-test-error"}>{testResult.message}</p>}</div></div>;
  }

  const config = (): ModelConfig => ({ providerId, apiFormat: "openai-chat-completions", baseUrl: baseUrl.trim(), model: model.trim(), apiKey: apiKey.trim() });
  const changeProvider = (next: ModelConfig["providerId"]) => {
    setProviderId(next);
    setBaseUrl(PRESETS[next].baseUrl);
    setModel(PRESETS[next].model);
    setTestResult(null);
  };

  return <div className="modal-backdrop" role="dialog" aria-modal="true">
    <form className="modal" onSubmit={(event) => { event.preventDefault(); onSave(config()); }}>
      <button type="button" className="icon-button modal-close" onClick={onClose} aria-label="关闭"><X size={18} /></button>
      <div className="modal-icon"><KeyRound /></div><h2>配置模型服务</h2>
      <p>API Key 仅保存在当前标签页的浏览器会话中，每次执行通过 HTTPS 请求头提交，后端不会持久化。</p>
      <label>Provider<select value={providerId} onChange={(event) => changeProvider(event.target.value as ModelConfig["providerId"])}><option value="deepseek">DeepSeek</option><option value="openai">OpenAI</option><option value="custom">自定义 OpenAI-compatible</option></select></label>
      <label>API 格式<input readOnly value="OpenAI Chat Completions" /></label>
      <label>Base URL<input required type="url" value={baseUrl} onChange={(event) => { setBaseUrl(event.target.value); setTestResult(null); }} /></label>
      <label>Model<input required value={model} onChange={(event) => { setModel(event.target.value); setTestResult(null); }} /></label>
      <label>API Key<input required type="password" autoComplete="off" value={apiKey} onChange={(event) => { setApiKey(event.target.value); setTestResult(null); }} /></label>
      <div className="model-config-actions"><button type="button" className="secondary-button" disabled={testing || !apiKey.trim() || !baseUrl.trim() || !model.trim()} onClick={() => { setTesting(true); setTestResult(null); void testModelConfig(config()).then((result) => setTestResult({ ok: true, message: `连接成功 · ${result.model}` })).catch((error) => setTestResult({ ok: false, message: error instanceof Error ? error.message : "连接失败" })).finally(() => setTesting(false)); }}>{testing ? "正在测试…" : "测试连接"}</button><button className="primary-button" type="submit">保存配置</button></div>
      {testResult && <p className={testResult.ok ? "model-test-success" : "model-test-error"}>{testResult.message}</p>}
      {initial && <button type="button" className="clear-model-config" onClick={onClear}><Trash2 size={15} />清除当前会话配置</button>}
      <small className="model-security-note">关闭当前标签页后配置会被清除。请只在可信设备和 HTTPS 页面中填写 API Key。</small>
    </form>
  </div>;
}
