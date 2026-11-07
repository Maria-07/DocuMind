"use client";

import Link from "next/link";
import { useState } from "react";
import { AlertTriangle, Database, FileText, MessageSquare, ShieldAlert, ShieldCheck } from "lucide-react";

import { Bars, LineTrend, ms, pct, shortDate, StatTile } from "@/components/charts";
import { EmptyState, ErrorState, PageBody, PageHeader } from "@/components/common";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useDocumentAnalytics, useOverview, useRetrievalAnalytics } from "@/features/insights/queries";
import { errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatBytes, timeAgo } from "@/lib/format";

const ACTION_LABEL: Record<string, string> = {
  "document.uploaded": "Uploaded",
  "document.deleted": "Deleted",
  "document.reprocess_requested": "Reprocess requested",
  "document.visibility_changed": "Visibility changed",
  "document.permissions_changed": "Sharing changed",
  "document.downloaded": "Downloaded",
  "document.previewed": "Previewed",
};

export default function AnalyticsPage() {
  const { can } = useAuth();
  const [days, setDays] = useState(30);
  const overview = useOverview();
  const docs = useDocumentAnalytics(days);
  const retrieval = useRetrievalAnalytics(days);

  if (!can("analytics:organization")) {
    return (
      <PageBody>
        <EmptyState icon={ShieldAlert} title="Administrators only" description="Organization analytics are available to admins." />
      </PageBody>
    );
  }

  const o = overview.data;
  const d = docs.data;
  const r = retrieval.data;
  const docSeries = d?.days.map((day, i) => ({ day, uploads: d.uploads[i], indexed: d.indexed[i] })) ?? [];
  const failedTotal = d?.failed.reduce((a, b) => a + b, 0) ?? 0;
  const querySeries = r?.days.map((day, i) => ({ day, queries: r.queries[i] })) ?? [];
  const noAnswerSeries =
    r?.days.map((day, i) => ({ day, rate: r.queries[i] ? r.no_answer[i] / r.queries[i] : null })) ?? [];
  const latency = r
    ? (
        [
          ["Embed", r.retrieval_latency_ms.embed],
          ["Vector search", r.retrieval_latency_ms.search],
          ["Permission check", r.retrieval_latency_ms.postcheck],
          ["Rerank", r.retrieval_latency_ms.rerank],
        ] as [string, number | null][]
      )
        .filter(([, v]) => v != null)
        .map(([stage, v]) => ({ stage, ms: Math.round(v as number) }))
    : [];

  return (
    <>
      <PageHeader
        title="Analytics"
        description="Documents, retrieval quality and usage across your organization."
        actions={
          <Select value={String(days)} onValueChange={(v) => setDays(Number(v))}>
            <SelectTrigger className="w-40" aria-label="Time range">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="7">Last 7 days</SelectItem>
              <SelectItem value="30">Last 30 days</SelectItem>
              <SelectItem value="90">Last 90 days</SelectItem>
            </SelectContent>
          </Select>
        }
      />
      <PageBody>
        {(overview.isError || docs.isError || retrieval.isError) && (
          <ErrorState message={errorMessage(overview.error ?? docs.error ?? retrieval.error)} />
        )}

        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {o ? (
            <>
              <StatTile label="Documents" value={o.documents_total} hint={`${o.documents_by_status.indexed ?? 0} indexed`} icon={<FileText className="size-4" />} />
              <StatTile label="Indexed chunks" value={o.chunks_total.toLocaleString()} hint={formatBytes(o.storage_bytes) + " stored"} icon={<Database className="size-4" />} />
              <StatTile label="Questions (7 days)" value={o.questions_7d} hint={`${o.conversations_total} conversations`} icon={<MessageSquare className="size-4" />} />
              <StatTile
                label="Grounded answers (7 days)"
                value={o.grounded_rate_7d == null ? "—" : pct(o.grounded_rate_7d)}
                hint="Answers backed by citations"
                icon={<ShieldCheck className="size-4" />}
              />
            </>
          ) : (
            Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-24" />)
          )}
        </div>

        <div className="grid gap-6 xl:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Document activity</CardTitle>
              <CardDescription>Uploads and successful indexing per day</CardDescription>
            </CardHeader>
            <CardContent>
              {d ? <LineTrend data={docSeries} xKey="day" xFormat={shortDate} series={[
                { key: "uploads", label: "Uploaded", color: "var(--series-1)" },
                { key: "indexed", label: "Indexed", color: "var(--series-2)" },
              ]} /> : <Skeleton className="h-60" />}
              {failedTotal > 0 && (
                <p className="mt-3 flex items-center gap-2 text-sm">
                  <AlertTriangle className="size-4" style={{ color: "var(--status-critical)" }} />
                  <span>
                    <strong>{failedTotal}</strong> document{failedTotal > 1 ? "s" : ""} failed processing in this period —{" "}
                    <Link href="/documents" className="text-primary hover:underline">review</Link>
                  </span>
                </p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Questions per day</CardTitle>
              <CardDescription>
                {r ? `${r.queries_total} questions and searches · ${r.answers_total} answers generated` : " "}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {r ? <Bars data={querySeries} xKey="day" xFormat={shortDate} series={[{ key: "queries", label: "Questions", color: "var(--series-1)" }]} /> : <Skeleton className="h-60" />}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>“Not found” rate</CardTitle>
              <CardDescription>
                Share of questions where no passage was relevant enough to answer
                {r?.no_answer_rate != null && ` · ${pct(r.no_answer_rate)} overall`}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {r ? <LineTrend data={noAnswerSeries} xKey="day" xFormat={shortDate} format={pct} yDomain={[0, 1]} series={[{ key: "rate", label: "Not found", color: "var(--series-1)" }]} /> : <Skeleton className="h-60" />}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Retrieval latency</CardTitle>
              <CardDescription>
                {r?.retrieval_latency_ms.p50 != null
                  ? `p50 ${ms(r.retrieval_latency_ms.p50)} · p95 ${ms(r.retrieval_latency_ms.p95 ?? 0)} · full answer p50 ${ms(r.answer_latency_ms.p50 ?? 0)}`
                  : "Average time per stage"}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {r ? (
                latency.length ? (
                  <Bars data={latency} xKey="stage" layout="vertical" format={ms} height={200} series={[{ key: "ms", label: "Average", color: "var(--series-1)" }]} />
                ) : (
                  <p className="py-16 text-center text-sm text-muted-foreground">No questions in this period.</p>
                )
              ) : (
                <Skeleton className="h-52" />
              )}
              {r && (
                <p className="mt-2 text-xs text-muted-foreground">
                  Tokens: {r.tokens.input.toLocaleString()} in · {r.tokens.output.toLocaleString()} out
                  {r.avg_filtered_by_postcheck != null && ` · avg ${r.avg_filtered_by_postcheck.toFixed(2)} hits removed by the permission check`}
                </p>
              )}
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Recent document activity</CardTitle>
          </CardHeader>
          <CardContent>
            {d?.recent_activity.length ? (
              <ul className="divide-y text-sm">
                {d.recent_activity.map((a, i) => (
                  <li key={i} className="flex items-center justify-between gap-3 py-2">
                    <span className="min-w-0 truncate">
                      <span className="font-medium">{ACTION_LABEL[a.action] ?? a.action}</span>
                      {a.title && <span className="text-muted-foreground"> · {a.title}</span>}
                    </span>
                    <span className="text-xs whitespace-nowrap text-muted-foreground">{timeAgo(a.at)}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">No activity yet.</p>
            )}
          </CardContent>
        </Card>
      </PageBody>
    </>
  );
}
