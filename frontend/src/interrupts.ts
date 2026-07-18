import type { ProductEvent } from "./types";

export interface ResumeWorkspaceSnapshot {
  resumeId: string;
  displayName: string;
  draft: string;
}

export interface PendingInterrupt {
  eventId: string;
  interruptId: string;
  phase: string;
  data: Record<string, unknown>;
  workspace: ResumeWorkspaceSnapshot | null;
}

function interruptData(event: ProductEvent): Record<string, unknown> | null {
  const interrupts = event.payload.interrupts;
  if (!Array.isArray(interrupts) || typeof interrupts[0] !== "object" || interrupts[0] === null) return null;
  return interrupts[0] as Record<string, unknown>;
}

function resumeInterrupt(event: ProductEvent): PendingInterrupt | null {
  const data = interruptData(event);
  if (!data || typeof data.phase !== "string" || typeof data.interrupt_id !== "string") return null;
  const rawWorkspace = data.workspace;
  const workspace = typeof rawWorkspace === "object" && rawWorkspace !== null
    ? rawWorkspace as Record<string, unknown>
    : null;
  return {
    eventId: event.eventId,
    interruptId: data.interrupt_id,
    phase: data.phase,
    data,
    workspace: workspace && typeof workspace.draft === "string"
      ? {
          resumeId: String(workspace.resumeId ?? ""),
          displayName: String(workspace.displayName ?? "简历工作草稿"),
          draft: workspace.draft,
        }
      : null,
  };
}

export function pendingInterrupt(events: ProductEvent[]): PendingInterrupt | null {
  let pending: PendingInterrupt | null = null;
  for (const event of events) {
    if (event.type === "interrupt.requested") {
      pending = resumeInterrupt(event);
      continue;
    }
    if (event.type !== "interrupt.resolved" || pending === null) continue;
    const decision = event.payload.decision;
    const decisionId = typeof event.payload.interruptId === "string"
      ? event.payload.interruptId
      : typeof decision === "object" && decision !== null
        ? String((decision as Record<string, unknown>).interrupt_id ?? "")
        : "";
    if (decisionId === pending.interruptId) pending = null;
  }
  return pending;
}

export function latestResumeWorkspace(events: ProductEvent[]): PendingInterrupt | null {
  let latest: PendingInterrupt | null = null;
  for (const event of events) {
    if (event.type === "interrupt.requested") {
      const candidate = resumeInterrupt(event);
      if (isResumeInterrupt(candidate)) latest = candidate;
      continue;
    }
    if (event.type !== "interrupt.resolved" || latest?.phase !== "resume_approve") continue;
    const decision = event.payload.decision;
    if (typeof decision !== "object" || decision === null) continue;
    if (String((decision as Record<string, unknown>).interrupt_id ?? "") !== latest.interruptId) continue;
    const action = String((decision as Record<string, unknown>).action ?? "");
    const draft = action === "approve" && typeof latest.data.after === "string"
      ? latest.data.after
      : latest.workspace?.draft;
    latest = {
      ...latest,
      eventId: event.eventId,
      phase: "resume_running",
      data: {},
      workspace: latest.workspace && draft !== undefined
        ? { ...latest.workspace, draft }
        : latest.workspace,
    };
  }
  return latest;
}

export function isResumeInterrupt(value: PendingInterrupt | null): boolean {
  return value?.phase === "plan_confirm" || value?.phase === "resume_approve" || value?.phase === "resume_hitl";
}
