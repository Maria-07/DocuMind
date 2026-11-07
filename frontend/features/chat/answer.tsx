"use client";

import Link from "next/link";
import { memo, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { BookOpen, FileText, ShieldAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { CitationRead, SourceRead } from "@/types";

function citationLocation(c: { page_start?: number | null; page_end?: number | null; sheet_name?: string | null; row_start?: number | null; row_end?: number | null; section_path?: string | null }) {
  const parts: string[] = [];
  if (c.page_start != null) parts.push(c.page_end && c.page_end !== c.page_start ? `pp. ${c.page_start}–${c.page_end}` : `p. ${c.page_start}`);
  if (c.sheet_name) parts.push(`${c.sheet_name}${c.row_start != null ? `, rows ${c.row_start}–${c.row_end}` : ""}`);
  if (c.section_path) parts.push(c.section_path);
  return parts.join(" · ");
}

function CitationChip({ citation }: { citation: CitationRead }) {
  const href = citation.url ?? (citation.document_id ? `/documents/${citation.document_id}` : "#");
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Link
          href={href}
          className="mx-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded-md bg-primary/10 px-1 align-text-top text-[11px] font-semibold text-primary no-underline hover:bg-primary hover:text-primary-foreground"
          aria-label={`Source ${citation.index}: ${citation.document_title}`}
        >
          {citation.index}
        </Link>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-sm text-left">
        <p className="font-medium">{citation.document_title}</p>
        {citationLocation(citation) && <p className="text-xs opacity-80">{citationLocation(citation)}</p>}
        <p className="mt-1 line-clamp-5 text-xs opacity-90">“{citation.snippet}”</p>
      </TooltipContent>
    </Tooltip>
  );
}

/** Markdown answer with [n] markers rendered as clickable citation chips. */
export const Answer = memo(function Answer({ content, citations }: { content: string; citations: CitationRead[] }) {
  const byIndex = useMemo(() => new Map(citations.map((c) => [c.index, c])), [citations]);
  // [n] → link to #cite-n (only for citations that exist; anything else stays literal text).
  const markdown = useMemo(
    () => content.replace(/\[(\d{1,2})\]/g, (m, n) => (byIndex.has(Number(n)) ? `[${n}](#cite-${n})` : m)),
    [content, byIndex],
  );
  return (
    <div className="prose-answer text-sm leading-relaxed">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        // No raw HTML: model output is rendered as Markdown only.
        skipHtml
        components={{
          a: ({ href, children }) => {
            const m = /^#cite-(\d+)$/.exec(href ?? "");
            if (m) {
              const c = byIndex.get(Number(m[1]));
              return c ? <CitationChip citation={c} /> : <>{children}</>;
            }
            return (
              <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="text-primary underline">
                {children}
              </a>
            );
          },
          table: ({ children }) => (
            <div className="my-3 overflow-x-auto rounded-lg border">
              <table className="w-full text-left text-sm">{children}</table>
            </div>
          ),
          th: ({ children }) => <th className="border-b bg-muted px-3 py-2 font-medium">{children}</th>,
          td: ({ children }) => <td className="border-b px-3 py-2">{children}</td>,
          ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>,
          ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>,
          p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
          strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
          code: ({ children }) => <code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">{children}</code>,
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
});

export function CitationList({ citations }: { citations: CitationRead[] }) {
  if (!citations.length) return null;
  return (
    <div className="mt-3 space-y-1.5">
      <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <BookOpen className="size-3.5" /> Sources
      </p>
      <ol className="grid gap-1.5 sm:grid-cols-2">
        {citations.map((c) => (
          <li key={c.index}>
            <Link
              href={c.url ?? "#"}
              className="flex items-start gap-2 rounded-lg border bg-background p-2 text-xs transition-colors hover:border-primary/50 hover:bg-accent/40"
            >
              <span className="grid size-5 shrink-0 place-items-center rounded-md bg-primary/10 font-semibold text-primary">{c.index}</span>
              <span className="min-w-0">
                <span className="block truncate font-medium">{c.document_title}</span>
                {citationLocation(c) && <span className="block truncate text-muted-foreground">{citationLocation(c)}</span>}
              </span>
            </Link>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function SourcesPanel({ sources }: { sources: SourceRead[] }) {
  if (!sources.length) return null;
  return (
    <details className="group mt-2 text-xs">
      <summary className="cursor-pointer list-none text-muted-foreground hover:text-foreground">
        <FileText className="mr-1 inline size-3.5" /> {sources.length} retrieved passage{sources.length > 1 ? "s" : ""}
        <span className="ml-1 group-open:hidden">· show</span>
      </summary>
      <ul className="mt-2 space-y-2">
        {sources.map((s) => (
          <li key={s.chunk_id} className="rounded-lg border bg-muted/40 p-2">
            <Link href={`/documents/${s.document_id}?chunk=${s.chunk_id}`} className="font-medium hover:text-primary">
              {s.document_title}
            </Link>
            {s.location && <span className="text-muted-foreground"> · {s.location}</span>}
            {s.relevance != null && <span className="text-muted-foreground"> · relevance {(s.relevance * 100).toFixed(0)}%</span>}
            <p className="mt-1 line-clamp-3 text-muted-foreground">{s.snippet}</p>
          </li>
        ))}
      </ul>
    </details>
  );
}

export function GroundingBadge({ grounded }: { grounded: boolean }) {
  return grounded ? null : (
    <Badge variant="outline" className={cn("mt-2 gap-1 text-[11px] text-muted-foreground")}>
      <ShieldAlert className="size-3" /> Not backed by document citations
    </Badge>
  );
}
