export type IdentityKind = "guest" | "developer";
export type AgentSource = "user" | "main" | "resume" | "research" | "system";

export interface Identity {
  principal_id: string;
  kind: IdentityKind;
}

export interface ThreadRecord {
  id: string;
  title: string;
  status: "idle" | "running" | "waiting" | "interrupted";
  created_at: string;
  updated_at: string;
  selected_resume_id: string | null;
  active_mode: "chat" | "resume";
  active_agent: "main" | "resume" | "research";
}

export interface ResumeMetadata {
  id: string;
  original_name: string;
  display_name: string;
  created_at: string;
  updated_at: string;
  source_resume_id: string | null;
}

export interface ResumeDocument extends ResumeMetadata {
  content: string;
}

export interface KnowledgeResource {
  id: string;
  scope: "public" | "personal";
  sourceType: "builtin" | "upload" | "research";
  displayName: string;
  status: "pending" | "indexing" | "ready" | "failed" | "deleting" | "delete_failed";
  failureReason: string;
  createdAt: string;
  updatedAt: string;
  readOnly: boolean;
}

export interface KnowledgeDocument extends KnowledgeResource {
  content: string;
}

export interface ProductEvent {
  eventId: string;
  threadId: string;
  sequence: number;
  taskId: string | null;
  type: string;
  source: AgentSource;
  occurredAt: string;
  payload: Record<string, unknown>;
}

export interface EventPage {
  events: ProductEvent[];
  hasMore: boolean;
}

export interface ModelConfig {
  baseUrl: string;
  model: string;
  apiKey: string;
}

export type StreamFrame =
  | { kind: "event"; event: ProductEvent }
  | {
      kind: "delta";
      messageId: string;
      source: AgentSource;
      text: string;
    };

export interface ApiErrorBody {
  error?: {
    code?: string;
    message?: string;
    retryable?: boolean;
  };
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly retryable: boolean;

  constructor(message: string, code: string, status: number, retryable = false) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.retryable = retryable;
  }
}
