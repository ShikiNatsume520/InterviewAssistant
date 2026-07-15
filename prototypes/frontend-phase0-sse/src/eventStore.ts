import type { SseFrame } from "./sse.js";

export interface TimelineEvent {
  id: string;
  type: string;
  agent: "main" | "resume" | "research" | "system";
  text: string;
}

export interface TimelineState {
  ordered: TimelineEvent[];
  seenIds: Set<string>;
  lastEventId: string;
}

export function emptyTimeline(): TimelineState {
  return { ordered: [], seenIds: new Set(), lastEventId: "" };
}

export function reduceFrame(state: TimelineState, frame: SseFrame): TimelineState {
  if (!frame.id || state.seenIds.has(frame.id)) return state;
  const parsed = JSON.parse(frame.data) as Omit<TimelineEvent, "id" | "type">;
  const event: TimelineEvent = {
    id: frame.id,
    type: frame.event,
    agent: parsed.agent,
    text: parsed.text,
  };
  return {
    ordered: [...state.ordered, event],
    seenIds: new Set([...state.seenIds, frame.id]),
    lastEventId: frame.id,
  };
}

export function serializeTimeline(state: TimelineState): string {
  return JSON.stringify(state.ordered);
}

export function restoreTimeline(raw: string | null): TimelineState {
  if (!raw) return emptyTimeline();
  const ordered = JSON.parse(raw) as TimelineEvent[];
  return {
    ordered,
    seenIds: new Set(ordered.map((event) => event.id)),
    lastEventId: ordered.at(-1)?.id ?? "",
  };
}
