export interface SseFrame {
  id: string;
  event: string;
  data: string;
}

export class SseParser {
  private buffer = "";

  push(chunk: string): SseFrame[] {
    this.buffer += chunk;
    const frames: SseFrame[] = [];
    const separator = /\r?\n\r?\n/;

    while (true) {
      const match = separator.exec(this.buffer);
      if (!match || match.index === undefined) break;
      const raw = this.buffer.slice(0, match.index);
      this.buffer = this.buffer.slice(match.index + match[0].length);
      const parsed = parseFrame(raw);
      if (parsed) frames.push(parsed);
    }
    return frames;
  }

  finish(): SseFrame[] {
    const tail = this.buffer.trim();
    this.buffer = "";
    const frame = tail ? parseFrame(tail) : null;
    return frame ? [frame] : [];
  }
}

function parseFrame(raw: string): SseFrame | null {
  let id = "";
  let event = "message";
  const data: string[] = [];

  for (const line of raw.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "id") id = value;
    else if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }

  if (!id && data.length === 0) return null;
  return { id, event, data: data.join("\n") };
}

export async function consumePostSse(
  url: string,
  body: unknown,
  onFrame: (frame: SseFrame) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`SSE request failed: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    for (const frame of parser.push(decoder.decode(value, { stream: true }))) {
      onFrame(frame);
    }
  }
  for (const frame of parser.push(decoder.decode())) onFrame(frame);
  for (const frame of parser.finish()) onFrame(frame);
}
