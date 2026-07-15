import type { ModelConfig } from "./types";

const STORAGE_KEY = "ia:model-config";

export function loadModelConfig(): ModelConfig | null {
  const raw = sessionStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<ModelConfig>;
    if (!value.apiKey || !value.baseUrl || !value.model) return null;
    return {
      apiKey: value.apiKey,
      baseUrl: value.baseUrl,
      model: value.model,
    };
  } catch {
    return null;
  }
}

export function saveModelConfig(config: ModelConfig): void {
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(config));
}

export function clearModelConfig(): void {
  sessionStorage.removeItem(STORAGE_KEY);
}

export function modelHeaders(config: ModelConfig | null): HeadersInit {
  if (!config) return {};
  return {
    "X-IA-API-Key": config.apiKey,
    "X-IA-Base-URL": config.baseUrl,
    "X-IA-Model": config.model,
  };
}
