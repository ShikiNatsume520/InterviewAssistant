import { modelHeaders } from "./modelConfig";
import {
  ApiError,
  type ApiErrorBody,
  type EventPage,
  type Identity,
  type KnowledgeDocument,
  type KnowledgeResource,
  type ModelConfig,
  type ResumeDocument,
  type ResumeMetadata,
  type StreamFrame,
  type ThreadRecord,
} from "./types";

async function parseError(response: Response): Promise<ApiError> {
  let body: ApiErrorBody = {};
  try {
    body = (await response.json()) as ApiErrorBody;
  } catch {
    // Non-JSON reverse-proxy failures use the generic message below.
  }
  return new ApiError(
    body.error?.message ?? `请求失败（${response.status}）`,
    body.error?.code ?? "REQUEST_FAILED",
    response.status,
    body.error?.retryable ?? response.status >= 500,
  );
}

async function jsonRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function createGuestIdentity(): Promise<Identity> {
  return jsonRequest("/v1/identity/guest", { method: "POST" });
}

export function getIdentity(): Promise<Identity> {
  return jsonRequest("/v1/identity");
}

export function getCapabilities(): Promise<{ developerLogin: boolean }> {
  return jsonRequest("/v1/capabilities");
}

export function developerLogin(accessToken: string): Promise<Identity> {
  return jsonRequest("/v1/identity/developer", {
    method: "POST",
    body: JSON.stringify({ access_token: accessToken }),
  });
}

export function restoreGuest(): Promise<Identity> {
  return jsonRequest("/v1/identity/guest/reset", { method: "POST" });
}

export function listThreads(): Promise<ThreadRecord[]> {
  return jsonRequest("/v1/threads");
}

export function createThread(title = "新会话"): Promise<ThreadRecord> {
  return jsonRequest("/v1/threads", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
}

export function renameThread(id: string, title: string): Promise<ThreadRecord> {
  return jsonRequest(`/v1/threads/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

export function deleteThread(id: string): Promise<void> {
  return jsonRequest(`/v1/threads/${id}`, { method: "DELETE" });
}

export function selectThreadResume(threadId: string, resumeId: string | null): Promise<ThreadRecord> {
  return jsonRequest(`/v1/threads/${threadId}/selected-resume`, {
    method: "PUT",
    body: JSON.stringify({ resume_id: resumeId }),
  });
}

export function listResumes(): Promise<ResumeMetadata[]> {
  return jsonRequest("/v1/resumes");
}

export function listKnowledgeResources(): Promise<KnowledgeResource[]> {
  return jsonRequest("/v1/knowledge/resources");
}

export function readKnowledgeResource(id: string): Promise<KnowledgeDocument> {
  return jsonRequest(`/v1/knowledge/resources/${encodeURIComponent(id)}`);
}

export function uploadKnowledge(file: File, modelConfig: ModelConfig | null, scope: "personal" | "public" = "personal"): Promise<KnowledgeResource> {
  return file.text().then((content) => jsonRequest("/v1/knowledge/resources", {
    method: "POST",
    headers: modelHeaders(modelConfig),
    body: JSON.stringify({ original_name: file.name, content, scope }),
  }));
}

export function reindexKnowledge(id: string, modelConfig: ModelConfig | null): Promise<KnowledgeResource> {
  return jsonRequest(`/v1/knowledge/resources/${encodeURIComponent(id)}/reindex`, {
    method: "POST",
    headers: modelHeaders(modelConfig),
  });
}

export function deleteKnowledge(id: string): Promise<void> {
  return jsonRequest(`/v1/knowledge/resources/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function uploadResume(file: File): Promise<ResumeDocument> {
  return file.text().then((content) => jsonRequest("/v1/resumes", {
    method: "POST",
    body: JSON.stringify({ original_name: file.name, content }),
  }));
}

export function renameResume(id: string, displayName: string): Promise<ResumeMetadata> {
  return jsonRequest(`/v1/resumes/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ display_name: displayName }),
  });
}

export function deleteResume(id: string): Promise<void> {
  return jsonRequest(`/v1/resumes/${id}`, { method: "DELETE" });
}

export async function downloadResume(id: string, displayName: string): Promise<void> {
  const response = await fetch(`/v1/resumes/${id}/download`, { credentials: "same-origin" });
  if (!response.ok) throw await parseError(response);
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = displayName.toLowerCase().endsWith(".md") ? displayName : `${displayName}.md`;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function getEvents(
  threadId: string,
  cursor?: { after?: string; before?: string },
): Promise<EventPage> {
  const search = new URLSearchParams({ limit: "50" });
  if (cursor?.after) search.set("afterEventId", cursor.after);
  if (cursor?.before) search.set("beforeEventId", cursor.before);
  return jsonRequest(`/v1/threads/${threadId}/events?${search}`);
}

interface StreamOptions {
  threadId: string;
  message?: string;
  resumeId?: string | null;
  resumeValue?: Record<string, unknown>;
  checkpoint?: boolean;
  modelConfig: ModelConfig | null;
  signal: AbortSignal;
  onFrame: (frame: StreamFrame) => void;
}

export async function streamGraph(options: StreamOptions): Promise<void> {
  const response = await fetch(
    options.checkpoint ? "/v1/checkpoint" : "/v1/chat",
    {
      method: "POST",
      credentials: "same-origin",
      signal: options.signal,
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...modelHeaders(options.modelConfig),
      },
      body: JSON.stringify(
        options.checkpoint
          ? { thread_id: options.threadId }
          : {
              thread_id: options.threadId,
              message: options.message ?? "",
              resume_id: options.resumeId ?? null,
              resume_value: options.resumeValue,
            },
      ),
    },
  );
  if (!response.ok) throw await parseError(response);
  if (!response.body) throw new ApiError("响应不支持流式读取", "STREAM_UNAVAILABLE", 500);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      const data = block
        .split(/\r?\n/)
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (!data) continue;
      const frame = JSON.parse(data) as StreamFrame;
      if (frame.kind === "event" || frame.kind === "delta") {
        options.onFrame(frame);
      }
    }
    if (done) break;
  }
}
