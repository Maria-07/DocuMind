"use client";

import Link from "next/link";
import { useState } from "react";
import { AlertTriangle, CheckCircle2, CircleDashed, GitCompare, HelpCircle, Loader2, X } from "lucide-react";
import { toast } from "sonner";

import { EmptyState, FileIcon, PageBody, PageHeader } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useDocuments } from "@/features/documents/queries";
import { type ComparisonRead, useComparisons, useCreateComparison } from "@/features/insights/queries";
import { errorMessage } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { FileType } from "@/types";

interface Citation {
  source_id: number;
  document_title: string;
  location: string | null;
  snippet: string;
  url: string;
}
interface Finding {
  document_id: string;
  status: "present" | "absent" | "unclear";
  finding: string;
  citations: Citation[];
}
interface Aspect {
  aspect: string;
  findings: Finding[];
  difference: string;
  conflict: boolean;
}
interface Result {
  documents: { id: string; title: string; file_type: FileType }[];
  aspects: Aspect[];
  missing_or_inconsistent: string[];
  summary: string;
}

const STATUS = {
  present: { icon: CheckCircle2, label: "Covered", className: "text-success" },
  absent: { icon: CircleDashed, label: "Not covered", className: "text-muted-foreground" },
  unclear: { icon: HelpCircle, label: "Unclear", className: "text-warning" },
} as const;

function CitationChip({ c }: { c: Citation }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Link
          href={c.url}
          className="inline-flex h-5 min-w-5 items-center justify-center rounded bg-accent px-1 text-[11px] font-medium text-accent-foreground hover:bg-primary hover:text-primary-foreground"
        >
          {c.source_id}
        </Link>
      </TooltipTrigger>
      <TooltipContent className="max-w-sm">
        <p className="font-medium">{c.location ?? c.document_title}</p>
        <p className="mt-1 line-clamp-4 opacity-90">{c.snippet}</p>
      </TooltipContent>
    </Tooltip>
  );
}

function ComparisonView({ comparison }: { comparison: ComparisonRead }) {
  if (comparison.status === "failed") {
    return <p className="text-sm text-destructive">Comparison failed: {comparison.error ?? "unknown error"}</p>;
  }
  const r = comparison.result as unknown as Result;
  if (!r?.aspects) return null;
  return (
    <div className="space-y-4">
      {r.summary && <p className="text-sm leading-relaxed">{r.summary}</p>}
      <div className="overflow-x-auto rounded-xl border">
        <table className="w-full min-w-[640px] text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="w-44 p-3 text-left font-medium">Aspect</th>
              {r.documents.map((d) => (
                <th key={d.id} className="p-3 text-left font-medium">
                  <Link href={`/documents/${d.id}`} className="inline-flex items-center gap-2 hover:underline">
                    <FileIcon type={d.file_type} className="size-4" />
                    <span className="line-clamp-1">{d.title}</span>
                  </Link>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y">
            {r.aspects.map((a) => (
              <tr key={a.aspect} className={cn("align-top", a.conflict && "bg-warning/5")}>
                <td className="p-3">
                  <p className="font-medium">{a.aspect}</p>
                  {a.conflict && (
                    <Badge variant="outline" className="mt-1 border-warning/50 text-warning">
                      <AlertTriangle /> Conflict
                    </Badge>
                  )}
                  {a.difference && <p className="mt-2 text-xs text-muted-foreground">{a.difference}</p>}
                </td>
                {a.findings.map((f) => {
                  const s = STATUS[f.status] ?? STATUS.unclear;
                  const Icon = s.icon;
                  return (
                    <td key={f.document_id} className="p-3">
                      <p className={cn("mb-1 flex items-center gap-1 text-xs font-medium", s.className)}>
                        <Icon className="size-3.5" aria-hidden /> {s.label}
                      </p>
                      <p>{f.finding}</p>
                      {f.citations.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1" aria-label="Sources">
                          {f.citations.map((c) => (
                            <CitationChip key={c.source_id} c={c} />
                          ))}
                        </div>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {r.missing_or_inconsistent.length > 0 && (
        <div className="rounded-xl border p-4">
          <p className="mb-2 text-sm font-medium">Missing or inconsistent</p>
          <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
            {r.missing_or_inconsistent.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        {comparison.model && `${comparison.model} · `}
        {comparison.latency_ms != null && `${(comparison.latency_ms / 1000).toFixed(1)} s · `}
        Findings marked “Covered” cite their sources; findings without valid evidence are shown as unclear.
      </p>
    </div>
  );
}

export default function ComparePage() {
  const documents = useDocuments({ status: "indexed", page_size: 100, sort: "title" });
  const history = useComparisons();
  const create = useCreateComparison();
  const [selected, setSelected] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [current, setCurrent] = useState<ComparisonRead | null>(null);

  const docs = documents.data?.items ?? [];
  const title = (id: string) => docs.find((d) => d.id === id)?.title ?? "Document";
  const shown = current ?? history.data?.[0] ?? null;

  function run() {
    create.mutate(
      { document_ids: selected, ...(question.trim() ? { question: question.trim() } : {}) },
      { onSuccess: setCurrent, onError: (e) => toast.error(errorMessage(e)) },
    );
  }

  return (
    <>
      <PageHeader title="Compare documents" description="Side-by-side, source-grounded comparison of 2–5 documents." />
      <PageBody>
        <Card>
          <CardHeader>
            <CardTitle>New comparison</CardTitle>
            <CardDescription>Pick the documents, optionally with a focus question.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-2">
              {selected.map((id) => (
                <Badge key={id} variant="secondary" className="gap-1 py-1 pr-1 pl-2.5 text-sm">
                  {title(id)}
                  <button
                    aria-label={`Remove ${title(id)}`}
                    className="rounded p-0.5 text-muted-foreground hover:text-foreground"
                    onClick={() => setSelected((s) => s.filter((x) => x !== id))}
                  >
                    <X className="size-3.5" />
                  </button>
                </Badge>
              ))}
              {selected.length < 5 && (
                <Select value="" onValueChange={(id) => setSelected((s) => [...s, id])}>
                  <SelectTrigger className="w-64" aria-label="Add document">
                    <SelectValue placeholder={docs.length ? "Add a document…" : "No indexed documents"} />
                  </SelectTrigger>
                  <SelectContent>
                    {docs
                      .filter((d) => !selected.includes(d.id))
                      .map((d) => (
                        <SelectItem key={d.id} value={d.id}>
                          {d.title}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              )}
            </div>
            <Textarea
              placeholder="Optional: what should the comparison focus on? e.g. “How do retention periods differ?”"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              maxLength={2000}
              rows={2}
              aria-label="Focus question"
            />
            <Button onClick={run} disabled={selected.length < 2 || create.isPending || (!!question.trim() && question.trim().length < 3)}>
              {create.isPending ? <Loader2 className="animate-spin" /> : <GitCompare />}
              {create.isPending ? "Comparing…" : "Compare"}
            </Button>
          </CardContent>
        </Card>

        {shown ? (
          <Card>
            <CardHeader>
              <CardTitle>{shown.question ?? "General comparison"}</CardTitle>
              <CardDescription>{timeAgo(String(shown.created_at))}</CardDescription>
            </CardHeader>
            <CardContent>
              <ComparisonView comparison={shown} />
            </CardContent>
          </Card>
        ) : (
          !history.isLoading && (
            <EmptyState icon={GitCompare} title="No comparisons yet" description="Select at least two indexed documents to compare them." />
          )
        )}

        {(history.data?.length ?? 0) > 1 && (
          <Card>
            <CardHeader>
              <CardTitle>History</CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="divide-y text-sm">
                {history.data!.map((c) => (
                  <li key={c.id}>
                    <button
                      className={cn(
                        "flex w-full items-center justify-between gap-3 py-2 text-left hover:text-primary",
                        shown?.id === c.id && "font-medium text-primary",
                      )}
                      onClick={() => setCurrent(c)}
                    >
                      <span className="min-w-0 truncate">
                        {c.question ?? "General comparison"} · {c.document_ids.length} documents
                        {c.status === "failed" && <span className="text-destructive"> · failed</span>}
                      </span>
                      <span className="text-xs whitespace-nowrap text-muted-foreground">{timeAgo(String(c.created_at))}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}
      </PageBody>
    </>
  );
}
