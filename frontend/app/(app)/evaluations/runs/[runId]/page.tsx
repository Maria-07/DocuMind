"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { ArrowDown, ArrowLeft, ArrowUp, ChevronDown, ChevronRight, Minus } from "lucide-react";

import { ms, pct } from "@/components/charts";
import { ErrorState, PageBody } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { improvement, METRICS, num } from "@/features/insights/metrics";
import { type EvalRunDetail, useRun } from "@/features/insights/queries";
import { RunStatus } from "@/features/insights/run-status";
import { errorMessage } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

const MAX_DROP = 0.05; // same default as `documind evaluate --max-drop`

function Delta({ value }: { value: number }) {
  if (Math.abs(value) < 0.005) {
    return (
      <span className="inline-flex items-center gap-0.5 text-xs text-muted-foreground">
        <Minus className="size-3" aria-hidden /> no change
      </span>
    );
  }
  const better = value > 0;
  const Icon = better ? ArrowUp : ArrowDown;
  return (
    <span className={cn("inline-flex items-center gap-0.5 text-xs font-medium", better ? "text-success" : value < -MAX_DROP ? "text-destructive" : "text-muted-foreground")}>
      <Icon className="size-3" aria-hidden />
      {Math.round(Math.abs(value) * 100)} pts {better ? "better" : "worse"}
    </span>
  );
}

function Summary({ run }: { run: EvalRunDetail }) {
  const base = run.baseline_metrics ?? null;
  const regressions = METRICS.filter((m) => {
    const c = num(run.metrics, m.key);
    const b = num(base, m.key);
    return c != null && b != null && improvement(m, c, b) < -MAX_DROP;
  });
  return (
    <>
      {base && (
        <p className={cn("text-sm", regressions.length ? "text-destructive" : "text-success")}>
          {regressions.length
            ? `Regression vs. baseline: ${regressions.map((m) => m.label).join(", ")} dropped more than ${MAX_DROP * 100} pts.`
            : "No regressions against the baseline run."}
        </p>
      )}
      {(["Retrieval", "Answer"] as const).map((group) => (
        <div key={group}>
          <h3 className="mb-2 text-sm font-medium text-muted-foreground">{group}</h3>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {METRICS.filter((m) => m.group === group).map((m) => {
              const v = num(run.metrics, m.key);
              const b = num(base, m.key);
              return (
                <div key={m.key} className="rounded-xl border bg-card p-3" title={m.description}>
                  <p className="text-sm text-muted-foreground">{m.label}</p>
                  <p className="mt-0.5 text-xl font-semibold tabular-nums">{v == null ? "—" : pct(v)}</p>
                  {v != null && b != null ? <Delta value={improvement(m, v, b)} /> : <p className="text-xs text-muted-foreground">{m.description}</p>}
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </>
  );
}

function CaseRow({ r }: { r: EvalRunDetail["results"][number] }) {
  const [open, setOpen] = useState(false);
  const judge = r.judge as { rationale?: string; unsupported_claims?: string[]; abstained?: boolean };
  const m = r.metrics;
  const cell = (key: string) => {
    const v = num(m, key);
    return v == null ? "—" : pct(v);
  };
  const abstainOk = num(m, "answer.abstention_accuracy");
  return (
    <>
      <TableRow className="cursor-pointer" onClick={() => setOpen(!open)} aria-expanded={open}>
        <TableCell className="w-6">{open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}</TableCell>
        <TableCell className="max-w-md">
          <span className="line-clamp-2">{r.question}</span>
          {r.error && <span className="text-xs text-destructive">{r.error}</span>}
        </TableCell>
        <TableCell className="text-right tabular-nums">{cell("retrieval.ndcg@5")}</TableCell>
        <TableCell className="text-right tabular-nums">{cell("answer.faithfulness")}</TableCell>
        <TableCell className="text-right tabular-nums">{cell("answer.citation_precision")}</TableCell>
        <TableCell>
          {abstainOk == null ? "—" : abstainOk ? <Badge variant="secondary" className="bg-success/12 text-success">Correct</Badge> : <Badge variant="destructive">Wrong</Badge>}
        </TableCell>
        <TableCell className="text-right text-muted-foreground tabular-nums">{r.latency_ms == null ? "—" : ms(r.latency_ms)}</TableCell>
      </TableRow>
      {open && (
        <TableRow className="hover:bg-transparent">
          <TableCell />
          <TableCell colSpan={6} className="space-y-3 pb-4 whitespace-normal">
            <div>
              <p className="text-xs font-medium text-muted-foreground">Answer</p>
              <p className="text-sm whitespace-pre-wrap">{r.answer ?? "—"}</p>
            </div>
            {judge?.rationale && (
              <div>
                <p className="text-xs font-medium text-muted-foreground">Judge rationale</p>
                <p className="text-sm">{judge.rationale}</p>
              </div>
            )}
            {!!judge?.unsupported_claims?.length && (
              <div>
                <p className="text-xs font-medium text-muted-foreground">Unsupported claims</p>
                <ul className="list-disc pl-5 text-sm text-destructive">
                  {judge.unsupported_claims.map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
              </div>
            )}
            {r.citations.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {(r.citations as { index: number; chunk_id: string; document_id: string; document_title: string; location: string | null }[]).map((c) => (
                  <Link key={c.index} href={`/documents/${c.document_id}?chunk=${c.chunk_id}`}>
                    <Badge variant="outline" className="hover:border-primary">
                      [{c.index}] {c.document_title}
                      {c.location && ` · ${c.location}`}
                    </Badge>
                  </Link>
                ))}
              </div>
            )}
            <p className="text-xs text-muted-foreground">
              P@5 {cell("retrieval.precision@5")} · R@5 {cell("retrieval.recall@5")} · MRR {cell("retrieval.mrr")} · answer relevance{" "}
              {cell("answer.answer_relevance")} · context relevance {cell("answer.context_relevance")} · correctness {cell("answer.correctness")}
              {r.top_relevance != null && ` · top relevance ${r.top_relevance.toFixed(4)}`}
            </p>
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

export default function RunPage() {
  const { runId } = useParams<{ runId: string }>();
  const { data: run, isLoading, isError, error } = useRun(runId);

  if (isLoading) return <PageBody><Skeleton className="h-96" /></PageBody>;
  if (isError || !run) return <PageBody><ErrorState message={errorMessage(error)} /></PageBody>;
  const cfg = run.config as Record<string, unknown>;

  return (
    <>
      <div className="border-b bg-card px-4 py-5 sm:px-8">
        <Link href={`/evaluations/${run.dataset_id}`} className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" /> Dataset
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-semibold tracking-tight">Evaluation run</h1>
          <RunStatus status={run.status} />
          <span className="text-sm text-muted-foreground">{formatDate(run.created_at)}</span>
        </div>
      </div>
      <PageBody>
        {run.error && <ErrorState message={run.error} />}
        {(run.status === "running" || run.status === "queued") && (
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">
              {run.completed_cases} of {run.case_count || "?"} cases evaluated
            </p>
            <Progress value={run.case_count ? (run.completed_cases / run.case_count) * 100 : 0} />
          </div>
        )}
        {run.status === "complete" && (
          <Card>
            <CardHeader>
              <CardTitle>Metrics</CardTitle>
              <CardDescription>
                Mean over {run.case_count} case{run.case_count === 1 ? "" : "s"}{run.baseline_metrics ? " · compared with the previous completed run" : ""}. Hover a tile for its definition.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <Summary run={run} />
            </CardContent>
          </Card>
        )}

        {run.results.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>Per-case results</CardTitle>
              <CardDescription>Click a row for the answer, citations and the judge’s rationale.</CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead />
                    <TableHead>Question</TableHead>
                    <TableHead className="text-right">NDCG@5</TableHead>
                    <TableHead className="text-right">Faithful</TableHead>
                    <TableHead className="text-right">Citations</TableHead>
                    <TableHead>Abstention</TableHead>
                    <TableHead className="text-right">Latency</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {run.results.map((r) => (
                    <CaseRow key={r.id} r={r} />
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        )}

        <Card>
          <CardHeader>
            <CardTitle>Configuration</CardTitle>
            <CardDescription>Everything that can change results, recorded with the run.</CardDescription>
          </CardHeader>
          <CardContent>
            <dl className="grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-[auto_1fr]">
              {Object.entries(cfg).map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-muted-foreground">{k.replaceAll("_", " ")}</dt>
                  <dd className="font-mono text-xs break-all">{v == null ? "—" : String(v)}</dd>
                </div>
              ))}
            </dl>
          </CardContent>
        </Card>
      </PageBody>
    </>
  );
}
