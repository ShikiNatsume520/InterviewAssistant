import { emptyTimeline, reduceFrame, restoreTimeline, serializeTimeline } from "./.probe-dist/eventStore.js";
import { consumePostSse } from "./.probe-dist/sse.js";

let first = emptyTimeline();
await consumePostSse("http://127.0.0.1:4177/api/events", { afterEventId: "" }, (frame) => {
  first = reduceFrame(first, frame);
});

if (first.ordered.length !== 5) throw new Error(`dedupe failed: ${first.ordered.length}`);
if (first.lastEventId !== "evt-5") throw new Error(`last ID failed: ${first.lastEventId}`);

const restored = restoreTimeline(serializeTimeline(first));
let resumed = restored;
await consumePostSse("http://127.0.0.1:4177/api/events", { afterEventId: restored.lastEventId }, (frame) => {
  resumed = reduceFrame(resumed, frame);
});

if (resumed.ordered.length !== 5) throw new Error(`resume duplicate: ${resumed.ordered.length}`);
console.log("PASS: 分块 POST SSE 被正确解析");
console.log("PASS: 重复 evt-3 只保留一次");
console.log("PASS: 序列化恢复后从 evt-5 续传，没有重复事件");
