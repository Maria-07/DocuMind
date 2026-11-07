"use client";

import Link from "next/link";
import { useState } from "react";
import { FlaskConical, Loader2, Plus, ShieldAlert } from "lucide-react";
import { toast } from "sonner";

import { pct } from "@/components/charts";
import { EmptyState, ErrorState, PageBody, PageHeader } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { RunStatus } from "@/features/insights/run-status";
import { num } from "@/features/insights/metrics";
import { useDatasets, useEvalMutations } from "@/features/insights/queries";
import { errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { timeAgo } from "@/lib/format";

function CreateDataset() {
  const { createDataset } = useEvalMutations();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  return (
    <Card>
      <CardHeader>
        <CardTitle>New dataset</CardTitle>
        <CardDescription>A set of questions with known answers and evidence, run against the live pipeline.</CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            createDataset.mutate(
              { name: name.trim(), ...(description.trim() ? { description: description.trim() } : {}) },
              {
                onSuccess: () => {
                  setName("");
                  setDescription("");
                  toast.success("Dataset created");
                },
                onError: (err) => toast.error(errorMessage(err)),
              },
            );
          }}
        >
          <div className="space-y-2">
            <Label htmlFor="ds-name">Name</Label>
            <Input id="ds-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} required />
          </div>
          <div className="space-y-2">
            <Label htmlFor="ds-desc">Description</Label>
            <Textarea id="ds-desc" value={description} onChange={(e) => setDescription(e.target.value)} rows={2} maxLength={2000} />
          </div>
          <Button type="submit" disabled={!name.trim() || createDataset.isPending}>
            {createDataset.isPending ? <Loader2 className="animate-spin" /> : <Plus />} Create dataset
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

export default function EvaluationsPage() {
  const { can } = useAuth();
  const { data, isLoading, isError, error } = useDatasets();

  if (!can("evaluations:view")) {
    return (
      <PageBody>
        <EmptyState icon={ShieldAlert} title="Not available" description="Evaluations are available to managers and admins." />
      </PageBody>
    );
  }

  return (
    <>
      <PageHeader
        title="Evaluations"
        description="Measure retrieval and answer quality, and catch regressions when settings or models change."
      />
      <PageBody>
        {isError && <ErrorState message={errorMessage(error)} />}
        <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
          <div className="space-y-3">
            {isLoading && Array.from({ length: 3 }, (_, i) => <Skeleton key={i} className="h-20" />)}
            {data?.length === 0 && (
              <EmptyState icon={FlaskConical} title="No datasets yet" description="Create a dataset, add test questions, then run it." />
            )}
            {data?.map((d) => {
              const last = d.last_run;
              const ndcg = num(last?.metrics, "retrieval.ndcg@5");
              const faith = num(last?.metrics, "answer.faithfulness");
              return (
                <Link
                  key={d.id}
                  href={`/evaluations/${d.id}`}
                  className="block rounded-xl border bg-card p-4 transition-colors hover:border-primary/50"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium">{d.name}</span>
                    {last && <RunStatus status={last.status} />}
                  </div>
                  {d.description && <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{d.description}</p>}
                  <p className="mt-2 text-xs text-muted-foreground">
                    {d.case_count} case{d.case_count === 1 ? "" : "s"}
                    {last
                      ? ` · last run ${timeAgo(last.created_at)}${ndcg != null ? ` · NDCG@5 ${pct(ndcg)}` : ""}${faith != null ? ` · faithfulness ${pct(faith)}` : ""}`
                      : " · never run"}
                  </p>
                </Link>
              );
            })}
          </div>
          {can("evaluations:run") && <CreateDataset />}
        </div>
      </PageBody>
    </>
  );
}
