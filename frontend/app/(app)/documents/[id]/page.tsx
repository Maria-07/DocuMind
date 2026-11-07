"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { AlertTriangle, ArrowLeft, Download, Loader2, MessageSquare, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { ErrorState, FileIcon, PageBody, StatusBadge } from "@/components/common";
import { MarkdownTable } from "@/components/markdown-table";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useCreateConversation } from "@/features/chat/queries";
import {
  useDeleteDocument,
  useDocument,
  useDocumentChunks,
  useDocumentJobs,
  useReprocessDocument,
  useUpdateDocument,
} from "@/features/documents/queries";
import { downloadFile, errorMessage, fetchBlob } from "@/lib/api";
import { formatBytes, formatDate, timeAgo } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { ChunkRead, DocumentDetail, DocumentVisibility } from "@/types";

function location(c: ChunkRead) {
  const parts: string[] = [];
  if (c.page_start != null) parts.push(c.page_end && c.page_end !== c.page_start ? `pp. ${c.page_start}–${c.page_end}` : `p. ${c.page_start}`);
  if (c.sheet_name) parts.push(`${c.sheet_name}${c.row_start != null ? ` · rows ${c.row_start}–${c.row_end}` : ""}`);
  if (c.section_path) parts.push(c.section_path);
  return parts.join(" · ") || `Chunk ${c.chunk_index + 1}`;
}

function ChunkList({ document, highlight }: { document: DocumentDetail; highlight: string | null }) {
  // chunk_count > 0 also covers re-processing: the previous version's content stays available.
  const { data, isLoading, isError, error } = useDocumentChunks(document.id, document.status === "indexed" || document.chunk_count > 0);
  const target = useRef<HTMLDivElement>(null);

  useEffect(() => {
    target.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [data, highlight]);

  if (document.status !== "indexed" && !data) {
    return <p className="text-sm text-muted-foreground">Content becomes available once the document is indexed.</p>;
  }
  if (isLoading) return <Skeleton className="h-40 w-full" />;
  if (isError) return <ErrorState message={errorMessage(error)} />;
  if (!data?.items.length) return <p className="text-sm text-muted-foreground">No extracted content.</p>;

  return (
    <div className="space-y-3">
      {data.total > data.items.length && (
        <p className="text-xs text-muted-foreground">
          Showing the first {data.items.length} of {data.total} chunks.
        </p>
      )}
      {data.items.map((c) => {
        const active = c.id === highlight;
        return (
          <div
            key={c.id}
            ref={active ? target : undefined}
            id={`chunk-${c.id}`}
            className={cn(
              "rounded-xl border bg-card p-4 transition-shadow",
              active && "border-primary shadow-[0_0_0_3px] shadow-primary/20",
            )}
          >
            <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <Badge variant="secondary">{location(c)}</Badge>
              <span>{c.kind === "rows" ? "Spreadsheet rows" : c.kind === "table" ? "Table" : "Text"}</span>
              <span>· {c.token_count} tokens</span>
              {c.flags.includes("possible_prompt_injection") && (
                <Badge variant="outline" className="border-warning/50 text-warning">
                  <AlertTriangle /> Instruction-like text
                </Badge>
              )}
              {active && <Badge>Cited</Badge>}
            </div>
            {c.kind === "table" ? (
              <MarkdownTable markdown={c.text} />
            ) : (
              <pre className="font-sans text-sm leading-relaxed whitespace-pre-wrap">{c.text.replace(/\n{2,}/g, "\n")}</pre>
            )}
          </div>
        );
      })}
    </div>
  );
}

function PdfPreview({ id }: { id: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let objectUrl: string | null = null;
    fetchBlob(`/api/v1/documents/${id}/preview`)
      .then(({ blob }) => {
        objectUrl = URL.createObjectURL(new Blob([blob], { type: "application/pdf" }));
        setUrl(objectUrl);
      })
      .catch((e) => setError(errorMessage(e)));
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [id]);
  if (error) return <ErrorState message={error} />;
  if (!url) return <Skeleton className="h-[70vh] w-full" />;
  return <iframe src={url} title="PDF preview" className="h-[75vh] w-full rounded-xl border bg-white" />;
}

function Details({ document }: { document: DocumentDetail }) {
  const { data: jobs } = useDocumentJobs(document.id);
  const extracted = (document.metadata?.extracted ?? {}) as Record<string, unknown>;
  const warnings = (document.metadata?.warnings ?? []) as string[];
  const rows: [string, string][] = [
    ["File name", document.original_filename],
    ["Type", document.file_type.toUpperCase()],
    ["Size", formatBytes(document.size_bytes)],
    ["Uploaded", formatDate(document.created_at)],
    ["Indexed", document.indexed_at ? formatDate(document.indexed_at) : "—"],
    ["Chunks", String(document.chunk_count)],
    ...(document.page_count ? [["Pages", String(document.page_count)] as [string, string]] : []),
    ...(document.sheet_names?.length ? [["Sheets", document.sheet_names.join(", ")] as [string, string]] : []),
    ...Object.entries(extracted)
      .filter(([, v]) => typeof v === "string" || Array.isArray(v))
      .map(([k, v]) => [k[0].toUpperCase() + k.slice(1), Array.isArray(v) ? v.join(", ") : String(v)] as [string, string]),
  ];
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Metadata</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
            {rows.map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-muted-foreground">{k}</dt>
                <dd className="wrap-break-word">{v}</dd>
              </div>
            ))}
          </dl>
          {warnings.length > 0 && (
            <Alert className="mt-4">
              <AlertTriangle />
              <AlertTitle>Extraction notes</AlertTitle>
              <AlertDescription>
                <ul className="list-disc pl-4">
                  {warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              </AlertDescription>
            </Alert>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Processing history</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {jobs?.map((j) => (
            <div key={j.id} className="rounded-lg border p-3 text-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium capitalize">{j.status}</span>
                <span className="text-xs text-muted-foreground">{timeAgo(j.created_at)}</span>
              </div>
              <p className="text-xs text-muted-foreground">
                Version {j.index_version ?? "—"} · attempt {j.attempt}
                {typeof j.metrics?.chunks === "number" && ` · ${j.metrics.chunks} chunks`}
                {typeof j.metrics?.embed_s === "number" && ` · embedded in ${j.metrics.embed_s}s`}
              </p>
              {j.error && <p className="mt-1 text-xs text-destructive">{j.error}</p>}
            </div>
          ))}
          {!jobs?.length && <p className="text-sm text-muted-foreground">No processing jobs yet.</p>}
        </CardContent>
      </Card>
    </div>
  );
}

function DocumentView() {
  const { id } = useParams<{ id: string }>();
  const chunk = useSearchParams().get("chunk");
  const router = useRouter();
  const { data: doc, isLoading, isError, error } = useDocument(id);
  const reprocess = useReprocessDocument();
  const remove = useDeleteDocument();
  const update = useUpdateDocument();
  const createConversation = useCreateConversation();
  const [tab, setTab] = useState("content");

  if (isLoading) {
    return (
      <PageBody>
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-96 w-full" />
      </PageBody>
    );
  }
  if (isError || !doc) {
    return (
      <PageBody>
        <ErrorState message={errorMessage(error)} />
        <Button variant="outline" asChild>
          <Link href="/documents">
            <ArrowLeft /> Back to documents
          </Link>
        </Button>
      </PageBody>
    );
  }

  async function askAbout() {
    const conv = await createConversation.mutateAsync({ title: doc!.title, scope: { document_ids: [doc!.id] } });
    router.push(`/chat/${conv.id}`);
  }

  return (
    <>
      <div className="border-b bg-card px-4 py-5 sm:px-8">
        <Link href="/documents" className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" /> Documents
        </Link>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex min-w-0 items-center gap-3">
            <FileIcon type={doc.file_type} className="size-8" />
            <div className="min-w-0">
              <h1 className="truncate text-xl font-semibold tracking-tight">{doc.title}</h1>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                <StatusBadge status={doc.status} />
                <span>{formatBytes(doc.size_bytes)}</span>
                <span>· uploaded {timeAgo(doc.created_at)}</span>
              </div>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button onClick={askAbout} disabled={doc.status !== "indexed" || createConversation.isPending}>
              <MessageSquare /> Ask about this document
            </Button>
            <Button
              variant="outline"
              onClick={() => downloadFile(`/api/v1/documents/${doc.id}/download`, doc.original_filename).catch((e) => toast.error(errorMessage(e)))}
            >
              <Download /> Download
            </Button>
            {doc.can_manage && (
              <>
                <Button
                  variant="outline"
                  disabled={doc.status === "processing" || doc.status === "uploaded" || reprocess.isPending}
                  onClick={() =>
                    reprocess.mutate(doc.id, {
                      onSuccess: () => toast.success("Re-indexing started"),
                      onError: (e) => toast.error(errorMessage(e)),
                    })
                  }
                >
                  {reprocess.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />} Reprocess
                </Button>
                <Button
                  variant="destructive"
                  onClick={() => {
                    if (!confirm(`Delete "${doc.title}"?`)) return;
                    remove.mutate(doc.id, {
                      onSuccess: () => {
                        toast.success("Document deleted");
                        router.replace("/documents");
                      },
                      onError: (e) => toast.error(errorMessage(e)),
                    });
                  }}
                >
                  <Trash2 /> Delete
                </Button>
              </>
            )}
          </div>
        </div>
      </div>

      <PageBody>
        {doc.status === "failed" && doc.error_message && (
          <Alert variant="destructive">
            <AlertTriangle />
            <AlertTitle>Processing failed</AlertTitle>
            <AlertDescription>{doc.error_message}</AlertDescription>
          </Alert>
        )}
        {doc.status === "indexed" && doc.error_message && (
          <Alert>
            <AlertTriangle />
            <AlertTitle>The last re-index failed</AlertTitle>
            <AlertDescription>{doc.error_message} The previous version is still searchable.</AlertDescription>
          </Alert>
        )}

        {doc.can_manage && (
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className="text-muted-foreground">Visible to</span>
            <Select
              value={doc.visibility}
              onValueChange={(v) =>
                update.mutate(
                  { id: doc.id, visibility: v as DocumentVisibility },
                  { onSuccess: () => toast.success("Access updated"), onError: (e) => toast.error(errorMessage(e)) },
                )
              }
            >
              <SelectTrigger className="w-56" aria-label="Visibility">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="organization">Everyone in the organization</SelectItem>
                <SelectItem value="restricted">Restricted (shared only)</SelectItem>
                <SelectItem value="private">Private (me and admins)</SelectItem>
              </SelectContent>
            </Select>
          </div>
        )}

        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="content">Extracted content</TabsTrigger>
            {doc.file_type === "pdf" && <TabsTrigger value="preview">Preview</TabsTrigger>}
            <TabsTrigger value="details">Details</TabsTrigger>
          </TabsList>
          <TabsContent value="content" className="mt-4">
            <ChunkList document={doc} highlight={chunk} />
          </TabsContent>
          {doc.file_type === "pdf" && (
            <TabsContent value="preview" className="mt-4">
              <PdfPreview id={doc.id} />
            </TabsContent>
          )}
          <TabsContent value="details" className="mt-4">
            <Details document={doc} />
          </TabsContent>
        </Tabs>
      </PageBody>
    </>
  );
}

export default function DocumentPage() {
  return (
    <Suspense>
      <DocumentView />
    </Suspense>
  );
}
