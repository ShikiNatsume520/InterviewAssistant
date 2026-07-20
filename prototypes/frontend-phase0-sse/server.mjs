import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join } from "node:path";

const events = [
  ["evt-1", "message.user", "system", "用户消息已接收"],
  ["evt-2", "message.agent", "main", "Main Agent 开始回答"],
  ["evt-3", "agent.transition", "resume", "进入 Resume Agent"],
  ["evt-4", "resume.diff", "resume", "生成一条简历修改"],
  ["evt-5", "task.completed", "system", "本轮完成"],
];

function frame([id, type, agent, text]) {
  return `id: ${id}\nevent: ${type}\ndata: ${JSON.stringify({ agent, text })}\n\n`;
}

const server = createServer(async (request, response) => {
  if (request.url === "/api/events" && request.method === "POST") {
    let raw = "";
    for await (const chunk of request) raw += chunk;
    const after = JSON.parse(raw || "{}").afterEventId || "";
    const index = events.findIndex(([id]) => id === after);
    const pending = events.slice(index + 1);
    response.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    });

    for (const event of pending) {
      const value = frame(event);
      const split = Math.max(1, Math.floor(value.length / 2));
      response.write(value.slice(0, split));
      await new Promise((resolve) => setTimeout(resolve, 10));
      response.write(value.slice(split));
      if (event[0] === "evt-3") response.write(value); // 故意重复，验证去重
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    response.end();
    return;
  }

  const path = request.url === "/" ? "index.html" : request.url.slice(1);
  try {
    const content = await readFile(join("dist", path));
    const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css" };
    response.writeHead(200, { "Content-Type": types[extname(path)] || "application/octet-stream" });
    response.end(content);
  } catch {
    response.writeHead(404);
    response.end("not found");
  }
});

server.listen(4177, "127.0.0.1", () => console.log("PROBE_SERVER_READY http://127.0.0.1:4177"));
