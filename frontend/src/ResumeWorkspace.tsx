import { useMemo, useState } from "react";
import ReactMarkdown, { type Components, type ExtraProps } from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import type { PendingInterrupt } from "./interrupts";
import "./lapis.css";

type ViewMode = "source" | "compare";
type HighlightKind = "removed" | "added";

interface SourceRange {
  start: number;
  end: number;
}

const lapisSchema = {
  ...defaultSchema,
  tagNames: [...(defaultSchema.tagNames ?? []), "div", "span"],
  attributes: {
    ...defaultSchema.attributes,
    "*": [...(defaultSchema.attributes?.["*"] ?? []), "className", "alt"],
    a: [...(defaultSchema.attributes?.a ?? []), "href"],
    img: [...(defaultSchema.attributes?.img ?? []), "src", "alt", "className"],
  },
};

function locateRange(markdown: string, fragment: string): SourceRange | null {
  if (!fragment) return null;
  const start = markdown.indexOf(fragment);
  return start < 0 ? null : { start, end: start + fragment.length };
}

function highlightClass(
  node: ExtraProps["node"],
  range: SourceRange | null,
  kind: HighlightKind | null,
  className?: string,
): string | undefined {
  const start = node?.position?.start.offset;
  const end = node?.position?.end.offset;
  const overlaps = range !== null && start !== undefined && end !== undefined
    && start < range.end && end > range.start;
  return [className, overlaps && kind ? `lapis-diff-${kind}` : ""].filter(Boolean).join(" ") || undefined;
}

function markdownComponents(range: SourceRange | null, kind: HighlightKind | null): Components {
  return {
    p: ({ node, className, ...props }) => <p {...props} className={highlightClass(node, range, kind, className)} />,
    li: ({ node, className, ...props }) => <li {...props} className={highlightClass(node, range, kind, className)} />,
    h1: ({ node, className, ...props }) => <h1 {...props} className={highlightClass(node, range, kind, className)} />,
    h2: ({ node, className, ...props }) => <h2 {...props} className={highlightClass(node, range, kind, className)} />,
    h3: ({ node, className, ...props }) => <h3 {...props} className={highlightClass(node, range, kind, className)} />,
    div: ({ node, className, ...props }) => <div {...props} className={highlightClass(node, range, kind, className)} />,
    span: ({ node, className, ...props }) => <span {...props} className={highlightClass(node, range, kind, className)} />,
    a: ({ node, className, children, ...props }) => <a {...props} className={highlightClass(node, range, kind, className)} target="_blank" rel="noreferrer noopener">{children}</a>,
    img: ({ node, className, src, alt, ...props }) => {
      if (!src || (!src.startsWith("https://") && !src.startsWith("http://"))) {
        return <span className="lapis-image-placeholder">图片资源未上传：{src || alt || "未知图片"}</span>;
      }
      return <img {...props} className={highlightClass(node, range, kind, className)} src={src} alt={alt ?? ""} referrerPolicy="no-referrer" />;
    },
  };
}

function LapisMarkdown({ markdown, highlight }: { markdown: string; highlight?: { range: SourceRange | null; kind: HighlightKind } }) {
  return <article className="lapis-preview">
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeRaw, [rehypeSanitize, lapisSchema]]}
      components={markdownComponents(highlight?.range ?? null, highlight?.kind ?? null)}
    >{markdown}</ReactMarkdown>
  </article>;
}

export function ResumeWorkspace({ interrupt, onSelection }: { interrupt: PendingInterrupt | null; onSelection: (text: string) => void }) {
  const isDiff = interrupt?.phase === "resume_approve";
  const [mode, setMode] = useState<ViewMode>(isDiff ? "source" : "compare");
  const [compareAfter, setCompareAfter] = useState(true);
  const before = typeof interrupt?.data.before === "string" ? interrupt.data.before : interrupt?.workspace?.draft ?? "";
  const after = typeof interrupt?.data.after === "string" ? interrupt.data.after : before;
  const beforeText = String(interrupt?.data.before_text ?? "");
  const afterText = String(interrupt?.data.after_text ?? "");
  const source = interrupt?.workspace?.draft ?? before;
  const rendered = isDiff ? (compareAfter ? after : before) : source;
  const renderedHighlight = isDiff
    ? { range: locateRange(rendered, compareAfter ? afterText : beforeText), kind: compareAfter ? "added" as const : "removed" as const }
    : undefined;
  const sourceRange = locateRange(before, beforeText);
  const title = interrupt?.workspace?.displayName ?? "简历工作草稿";
  const counter = isDiff ? `修改 ${String(interrupt?.data.ordinal ?? 1)} / ${String(interrupt?.data.total ?? 1)}` : "Resume Agent";
  const captureSelection = () => onSelection(window.getSelection()?.toString().trim() ?? "");

  return <section className="resume-workspace" onMouseUp={captureSelection}>
    <header className="workspace-header"><div><strong>{title}</strong><span>源简历只读 · 工作草稿由 checkpoint 恢复</span></div><b>{counter}</b></header>
    <nav className="workspace-tabs">
      <button className={mode === "source" ? "active" : ""} onClick={() => setMode("source")}>源码{isDiff ? " Diff" : ""}</button>
      <button className={mode === "compare" ? "active" : ""} onClick={() => setMode("compare")}>渲染对比</button>
    </nav>
    {mode === "source" && <div className="workspace-source">
      {isDiff && sourceRange ? <pre className="source-diff-full">
        <span>{before.slice(0, sourceRange.start)}</span>
        <del>{beforeText}</del>
        <ins>{afterText}</ins>
        <span>{before.slice(sourceRange.end)}</span>
      </pre> : source ? <pre>{source}</pre> : <div className="workspace-loading">Resume Agent 正在准备工作草稿……</div>}
    </div>}
    {mode === "compare" && <div className="workspace-preview">
      {isDiff && <div className="compare-toggle"><button className={!compareAfter ? "active" : ""} onClick={() => setCompareAfter(false)}>修改前</button><button className={compareAfter ? "active" : ""} onClick={() => setCompareAfter(true)}>修改后</button></div>}
      {rendered ? <LapisMarkdown markdown={rendered} highlight={renderedHighlight} /> : <div className="workspace-loading">Resume Agent 正在准备工作草稿……</div>}
    </div>}
    <footer className="workspace-footer">已批准修改不可逐条撤销；可以在待命阶段放弃整份草稿。</footer>
  </section>;
}
