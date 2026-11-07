"use client";

import { AlertTriangle, BrainCircuit, CheckCircle2, Loader2, RefreshCw, ShieldAlert } from "lucide-react";
import { toast } from "sonner";

import { StatTile } from "@/components/charts";
import { EmptyState, ErrorState, PageBody, PageHeader } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useEmbeddings, useReindex } from "@/features/insights/queries";
import { errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDate } from "@/lib/format";

const MODES = {
  stale: { label: "Re-index stale", confirm: "Re-index documents embedded with an older configuration?" },
  failed: { label: "Retry failed", confirm: "Retry every document whose processing failed?" },
  all: { label: "Re-index everything", confirm: "Re-index every document? Search keeps using the current index until each finishes." },
} as const;

export default function EmbeddingsPage() {
  const { can } = useAuth();
  const { data, isLoading, isError, error } = useEmbeddings();
  const reindex = useReindex();

  if (!can("organization:manage")) {
    return (
      <PageBody>
        <EmptyState icon={ShieldAlert} title="Administrators only" description="Embedding configuration is managed by admins." />
      </PageBody>
    );
  }

  function run(mode: keyof typeof MODES) {
    if (!confirm(MODES[mode].confirm)) return;
    reindex.mutate(mode, {
      onSuccess: (r) => toast.success(r.queued ? `Queued ${r.queued} document${r.queued > 1 ? "s" : ""}` : "Nothing to re-index"),
      onError: (e) => toast.error(errorMessage(e)),
    });
  }

  return (
    <>
      <PageHeader
        title="Embeddings"
        description="Index configurations and coverage. Changing the model or chunking creates a new index; re-index to move documents onto it."
        actions={(Object.keys(MODES) as (keyof typeof MODES)[]).map((m) => (
          <Button key={m} variant={m === "stale" ? "default" : "outline"} disabled={reindex.isPending} onClick={() => run(m)}>
            {reindex.isPending && reindex.variables === m ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            {MODES[m].label}
          </Button>
        ))}
      />
      <PageBody>
        {isError && <ErrorState message={errorMessage(error)} />}
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {data ? (
            <>
              <StatTile label="On the active index" value={`${data.documents_on_active} / ${data.documents_total}`} icon={<CheckCircle2 className="size-4" />} />
              <StatTile label="Stale (older config)" value={data.documents_stale} hint={data.documents_stale ? "Searchable via their old index until re-indexed" : "All current"} icon={<RefreshCw className="size-4" />} />
              <StatTile label="Not yet indexed" value={data.documents_not_indexed} icon={<BrainCircuit className="size-4" />} />
              <StatTile label="Failed" value={data.documents_failed} icon={<AlertTriangle className="size-4" />} />
            </>
          ) : (
            Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-24" />)
          )}
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Configurations</CardTitle>
            <CardDescription>Each configuration has its own vector collection; queries use the active one.</CardDescription>
          </CardHeader>
          <CardContent>
            {isLoading && <Skeleton className="h-32" />}
            {data && (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Model</TableHead>
                      <TableHead>Chunking</TableHead>
                      <TableHead>Sparse</TableHead>
                      <TableHead className="text-right">Documents</TableHead>
                      <TableHead className="text-right">Chunks</TableHead>
                      <TableHead className="text-right">Vectors</TableHead>
                      <TableHead>Created</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.configs.map((c) => (
                      <TableRow key={c.id}>
                        <TableCell>
                          <div className="flex items-center gap-2 font-medium">
                            {c.embedding_model}
                            {c.is_active && <Badge>Active</Badge>}
                          </div>
                          <p className="font-mono text-xs text-muted-foreground">
                            {c.embedding_provider} · {c.dim} dims · {c.collection}
                          </p>
                        </TableCell>
                        <TableCell className="tabular-nums">
                          {c.chunk_size} tokens, {c.chunk_overlap} overlap
                        </TableCell>
                        <TableCell>{c.sparse_model}</TableCell>
                        <TableCell className="text-right tabular-nums">{c.documents}</TableCell>
                        <TableCell className="text-right tabular-nums">{c.chunks.toLocaleString()}</TableCell>
                        <TableCell className="text-right tabular-nums">
                          {c.points == null ? "—" : c.points.toLocaleString()}
                          {c.points != null && c.points !== c.chunks && (
                            <AlertTriangle className="ml-1 inline size-3.5 text-warning" aria-label="Vector count differs from chunk count" />
                          )}
                        </TableCell>
                        <TableCell className="text-muted-foreground">{formatDate(c.created_at)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
      </PageBody>
    </>
  );
}
