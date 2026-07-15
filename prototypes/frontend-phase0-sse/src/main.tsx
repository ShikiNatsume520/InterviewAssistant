import { StrictMode, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  emptyTimeline,
  reduceFrame,
  restoreTimeline,
  serializeTimeline,
  type TimelineState,
} from "./eventStore";
import { consumePostSse } from "./sse";
import "./style.css";

const STORAGE_KEY = "phase0-sse-timeline";

function App() {
  const [timeline, setTimeline] = useState<TimelineState>(() =>
    restoreTimeline(sessionStorage.getItem(STORAGE_KEY)),
  );
  const [status, setStatus] = useState("idle");
  const controller = useRef<AbortController | null>(null);

  const connect = async () => {
    controller.current?.abort();
    controller.current = new AbortController();
    setStatus("streaming");
    try {
      await consumePostSse(
        "/api/events",
        { afterEventId: timeline.lastEventId },
        (frame) => setTimeline((current) => reduceFrame(current, frame)),
        controller.current.signal,
      );
      setStatus("done");
    } catch (error) {
      setStatus(error instanceof DOMException && error.name === "AbortError" ? "aborted" : "failed");
    }
  };

  useEffect(() => {
    sessionStorage.setItem(STORAGE_KEY, serializeTimeline(timeline));
  }, [timeline]);

  return (
    <main>
      <h1>React POST SSE 恢复探针</h1>
      <p>状态：{status} · 最后事件：{timeline.lastEventId || "无"}</p>
      <div className="actions">
        <button onClick={() => void connect()}>连接 / 恢复</button>
        <button onClick={() => controller.current?.abort()}>模拟断开</button>
        <button
          onClick={() => {
            sessionStorage.removeItem(STORAGE_KEY);
            setTimeline(emptyTimeline());
          }}
        >
          清空状态
        </button>
      </div>
      <ol>
        {timeline.ordered.map((event) => (
          <li key={event.id}>
            <strong>{event.id} · {event.agent}</strong> {event.text}
          </li>
        ))}
      </ol>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
