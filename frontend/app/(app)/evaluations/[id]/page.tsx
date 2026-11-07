"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { ArrowLeft, Loader2, Play, Plus, Trash2, Upload, X } from "lucide-react";
import { toast } from "sonner";

import { LineTrend, pct, shortDate } from "@/components/charts";
import { ErrorState, PageBody } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { useDocuments } from "@/features/documents/queries";
import { num } from "@/features/insights/metrics";
import {
  type EvalCaseInput,
  useCases,
  useDatasets,
  useEvalMutations,
  useMetricHistory,
  useRuns,
} from "@/features/insights/queries";
import { RunStatus } from "@/features/insights/run-status";
import { errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { timeAgo } from "@/lib/format";

const TREND = [
  { key: "retrieval.ndcg@5", label: "NDCG@5", color: "var(--series-1)" },
  { key: "answer.faithfulness", label: "Faithfulness", color: "var(--series-2)" },
  { key: "answer.abstention_accuracy", label: "Abstention accuracy", color: "var(--series-3)" },
];

function AddCase({ datasetId }: { datasetId: string }) {
  const { addCases } = useEvalMutations(datasetId);
  const documents = useDocuments({ status: "indexed", page_size: 100, sort: "title" });
  const docs = documents.data?.items ?? [];
  const empty = { question: "", expected: "", docIds: [] as string[], quotes: {} as Record<string, string>, tags: "" };
  const [f, setF] = useState(empty);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const body: EvalCaseInput = {
      question: f.question.trim(),
      expected_answer: f.expected.trim() || null,
      relevant_document_ids: f.docIds,
      evidence: f.docIds
        .filter((id) => (f.quotes[id] ?? "").trim().length >= 3)
        .map((id) => ({ document_id: id, quote: f.quotes[id].trim() })),
      tags: f.tags.split(",").map((t) => t.trim()).filter(Boolean),
    };
    addCases.mutate([body], {
      onSuccess: () => {
        setF(empty);
        toast.success("Case added");
      },
      onError: (err) => toast.error(errorMessage(err)),
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Add a test case</CardTitle>
        <CardDescription>
          Leave “relevant documents” empty for a question the documents should <em>not</em> answer (tests abstention).
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form className="space-y-4" onSubmit={submit}>
          <div className="space-y-2">
            <Label htmlFor="q">Question</Label>
            <Textarea id="q" rows={2} required minLength={3} maxLength={2000} value={f.question} onChange={(e) => setF({ ...f, question: e.target.value })} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="exp">Reference answer (optional)</Label>
            <Textarea id="exp" rows={2} maxLength={4000} value={f.expected} onChange={(e) => setF({ ...f, expected: e.target.value })} />
          </div>
          <div className="space-y-2">
            <Label>Relevant documents</Label>
            <Select value="" onValueChange={(id) => setF({ ...f, docIds: [...f.docIds, id] })}>
              <SelectTrigger className="w-full" aria-label="Add relevant document">
                <SelectValue placeholder="Add a document…" />
              </SelectTrigger>
              <SelectContent>
                {docs.filter((d) => !f.docIds.includes(d.id)).map((d) => (
                  <SelectItem key={d.id} value={d.id}>
                    {d.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {f.docIds.map((id) => (
              <div key={id} className="space-y-1.5 rounded-lg border p-3">
                <div className="flex items-center justify-between gap-2 text-sm font-medium">
                  <span className="truncate">{docs.find((d) => d.id === id)?.title ?? id}</span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label="Remove document"
                    onClick={() => setF({ ...f, docIds: f.docIds.filter((x) => x !== id) })}
                  >
                    <X />
                  </Button>
                </div>
                <Input
                  placeholder="Evidence quote — exact text that answers the question (optional, enables passage-level metrics)"
                  value={f.quotes[id] ?? ""}
                  maxLength={2000}
                  onChange={(e) => setF({ ...f, quotes: { ...f.quotes, [id]: e.target.value } })}
                  aria-label="Evidence quote"
                />
              </div>
            ))}
          </div>
          <div className="space-y-2">
            <Label htmlFor="tags">Tags (comma separated)</Label>
            <Input id="tags" value={f.tags} onChange={(e) => setF({ ...f, tags: e.target.value })} />
          </div>
          <Button type="submit" disabled={f.question.trim().length < 3 || addCases.isPending}>
            {addCases.isPending ? <Loader2 className="animate-spin" /> : <Plus />} Add case
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function ImportCases({ datasetId }: { datasetId: string }) {
  const { addCases } = useEvalMutations(datasetId);
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  function submit() {
    let cases: EvalCaseInput[];
    try {
      const parsed = JSON.parse(text);
      cases = Array.isArray(parsed) ? parsed : [parsed];
    } catch {
      toast.error("Not valid JSON");
      return;
    }
    addCases.mutate(cases, {
      onSuccess: (r) => {
        toast.success(`Imported ${r.length} case${r.length === 1 ? "" : "s"}`);
        setText("");
        setOpen(false);
      },
      onError: (e) => toast.error(errorMessage(e)),
    });
  }
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline">
          <Upload /> Import JSON
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Import test cases</DialogTitle>
          <DialogDescription>
            A JSON array (max 500) of objects with <code>question</code>, and optionally <code>expected_answer</code>,{" "}
            <code>relevant_document_ids</code>, <code>evidence</code> (<code>[{"{document_id, quote}"}]</code>) and <code>tags</code>.
          </DialogDescription>
        </DialogHeader>
        <Textarea rows={12} className="font-mono text-xs" value={text} onChange={(e) => setText(e.target.value)} aria-label="Cases JSON" />
        <DialogFooter>
          <Button onClick={submit} disabled={!text.trim() || addCases.isPending}>
            {addCases.isPending && <Loader2 className="animate-spin" />} Import
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Trend({ datasetId, completedRuns }: { datasetId: string; completedRuns: number }) {
  const { data } = useMetricHistory(datasetId, completedRuns);
  if (!data) return <Skeleton className="h-64" />;
  if (data.length === 0) return <p className="py-10 text-center text-sm text-muted-foreground">Run the dataset to start a trend.</p>;
  const rows = data.map((p) => ({
    at: p.finished_at?.slice(0, 10) ?? "",
    ...Object.fromEntries(TREND.map((t) => [t.key, num(p.metrics, t.key)])),
  }));
  return (
    <Tabs defaultValue="chart">
      <TabsList>
        <TabsTrigger value="chart">Chart</TabsTrigger>
        <TabsTrigger value="table">Table</TabsTrigger>
      </TabsList>
      <TabsContent value="chart" className="mt-3">
        <LineTrend data={rows} xKey="at" xFormat={shortDate} format={pct} yDomain={[0, 1]} series={TREND} />
      </TabsContent>
      <TabsContent value="table" className="mt-3">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Run</TableHead>
              {TREND.map((t) => (
                <TableHead key={t.key} className="text-right">
                  {t.label}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.map((p) => (
              <TableRow key={p.run_id}>
                <TableCell>
                  <Link href={`/evaluations/runs/${p.run_id}`} className="text-primary hover:underline">
                    {p.finished_at ? new Date(p.finished_at).toLocaleString() : "—"}
                  </Link>
                </TableCell>
                {TREND.map((t) => {
                  const v = num(p.metrics, t.key);
                  return (
                    <TableCell key={t.key} className="text-right tabular-nums">
                      {v == null ? "—" : pct(v)}
                    </TableCell>
                  );
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TabsContent>
    </Tabs>
  );
}

export default function DatasetPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { can } = useAuth();
  const datasets = useDatasets();
  const cases = useCases(id);
  const runs = useRuns(id);
  const { run, deleteCase, deleteDataset } = useEvalMutations(id);
  const dataset = datasets.data?.find((d) => d.id === id);
  const canRun = can("evaluations:run");
  const active = runs.data?.some((r) => r.status === "queued" || r.status === "running");

  if (datasets.isError) return <PageBody><ErrorState message={errorMessage(datasets.error)} /></PageBody>;
  if (datasets.data && !dataset) return <PageBody><ErrorState message="Dataset not found" /></PageBody>;

  return (
    <>
      <div className="border-b bg-card px-4 py-5 sm:px-8">
        <Link href="/evaluations" className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" /> Evaluations
        </Link>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            {dataset ? <h1 className="text-xl font-semibold tracking-tight">{dataset.name}</h1> : <Skeleton className="h-7 w-48" />}
            {dataset?.description && <p className="mt-0.5 text-sm text-muted-foreground">{dataset.description}</p>}
          </div>
          {canRun && (
            <div className="flex flex-wrap gap-2">
              <ImportCases datasetId={id} />
              <Button
                disabled={!cases.data?.length || run.isPending || active}
                onClick={() =>
                  run.mutate(undefined, {
                    onSuccess: () => toast.success("Evaluation queued"),
                    onError: (e) => toast.error(errorMessage(e)),
                  })
                }
              >
                {run.isPending || active ? <Loader2 className="animate-spin" /> : <Play />}
                {active ? "Running…" : "Run evaluation"}
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Delete dataset"
                onClick={() =>
                  confirm(`Delete dataset "${dataset?.name}" with its cases and runs?`) &&
                  deleteDataset.mutate(id, { onSuccess: () => router.replace("/evaluations"), onError: (e) => toast.error(errorMessage(e)) })
                }
              >
                <Trash2 />
              </Button>
            </div>
          )}
        </div>
      </div>
      <PageBody>
        <Card>
          <CardHeader>
            <CardTitle>Quality trend</CardTitle>
            <CardDescription>Completed runs, oldest to newest</CardDescription>
          </CardHeader>
          <CardContent>
            <Trend datasetId={id} completedRuns={runs.data?.filter((r) => r.status === "complete").length ?? 0} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Runs</CardTitle>
          </CardHeader>
          <CardContent>
            {runs.data?.length ? (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Started</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Cases</TableHead>
                    <TableHead className="text-right">NDCG@5</TableHead>
                    <TableHead className="text-right">Faithfulness</TableHead>
                    <TableHead>Model</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {runs.data.map((r) => {
                    const ndcg = num(r.metrics, "retrieval.ndcg@5");
                    const faith = num(r.metrics, "answer.faithfulness");
                    return (
                      <TableRow key={r.id} className="cursor-pointer" onClick={() => router.push(`/evaluations/runs/${r.id}`)}>
                        <TableCell>
                          <Link href={`/evaluations/runs/${r.id}`} className="text-primary hover:underline">
                            {timeAgo(r.created_at)}
                          </Link>
                        </TableCell>
                        <TableCell>
                          <RunStatus status={r.status} />
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {r.status === "running" ? `${r.completed_cases}/${r.case_count}` : r.case_count}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{ndcg == null ? "—" : pct(ndcg)}</TableCell>
                        <TableCell className="text-right tabular-nums">{faith == null ? "—" : pct(faith)}</TableCell>
                        <TableCell className="text-muted-foreground">{String(r.config?.llm_model ?? "—")}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            ) : (
              <p className="text-sm text-muted-foreground">No runs yet.</p>
            )}
          </CardContent>
        </Card>

        <div className="grid gap-6 lg:grid-cols-[1fr_420px]">
          <Card>
            <CardHeader>
              <CardTitle>Test cases ({cases.data?.length ?? 0})</CardTitle>
            </CardHeader>
            <CardContent>
              {cases.isLoading && <Skeleton className="h-32" />}
              {cases.data?.length === 0 && <p className="text-sm text-muted-foreground">No cases yet.</p>}
              <ul className="divide-y">
                {cases.data?.map((c) => (
                  <li key={c.id} className="flex gap-3 py-3 text-sm">
                    <div className="min-w-0 flex-1">
                      <p className="font-medium">{c.question}</p>
                      {c.expected_answer && <p className="mt-1 line-clamp-2 text-muted-foreground">{c.expected_answer}</p>}
                      <div className="mt-1.5 flex flex-wrap gap-1.5">
                        {c.relevant_document_ids.length ? (
                          <Badge variant="secondary">
                            {c.relevant_document_ids.length} relevant doc{c.relevant_document_ids.length > 1 ? "s" : ""}
                            {c.evidence.length > 0 && ` · ${c.evidence.length} quote${c.evidence.length > 1 ? "s" : ""}`}
                          </Badge>
                        ) : (
                          <Badge variant="outline">Should abstain</Badge>
                        )}
                        {c.tags.map((t) => (
                          <Badge key={t} variant="outline">
                            {t}
                          </Badge>
                        ))}
                      </div>
                    </div>
                    {canRun && (
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label="Delete case"
                        onClick={() => deleteCase.mutate(c.id, { onError: (e) => toast.error(errorMessage(e)) })}
                      >
                        <Trash2 />
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
          {canRun && <AddCase datasetId={id} />}
        </div>
      </PageBody>
    </>
  );
}
