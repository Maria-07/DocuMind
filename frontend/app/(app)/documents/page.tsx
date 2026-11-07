"use client";

import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, FileText, Search } from "lucide-react";

import { EmptyState, ErrorState, PageBody, PageHeader } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { DocumentTable } from "@/features/documents/document-table";
import { type DocumentFilters, useCategories, useDocuments } from "@/features/documents/queries";
import { UploadDialog } from "@/features/documents/upload-dialog";
import { errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { DocumentStatus, FileType } from "@/types";

const PAGE_SIZE = 20;

function useDebounced<T>(value: T, ms = 300) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export default function DocumentsPage() {
  const { can } = useAuth();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<DocumentStatus | "all">("all");
  const [fileType, setFileType] = useState<FileType | "all">("all");
  const [category, setCategory] = useState("all");
  const q = useDebounced(search);
  // The page belongs to one filter combination: changing any filter starts again at page 1.
  const filterKey = JSON.stringify([q, status, fileType, category]);
  const [pageState, setPageState] = useState({ key: filterKey, page: 1 });
  const page = pageState.key === filterKey ? pageState.page : 1;
  const setPage = (p: number) => setPageState({ key: filterKey, page: p });

  const filters: DocumentFilters = {
    q: q || undefined,
    status: status === "all" ? undefined : status,
    file_type: fileType === "all" ? undefined : fileType,
    category_id: category === "all" ? undefined : category,
    page,
    page_size: PAGE_SIZE,
  };
  const { data, isLoading, isError, error, refetch } = useDocuments(filters);
  const { data: categories } = useCategories();
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  const filtered = !!(q || status !== "all" || fileType !== "all" || category !== "all");

  return (
    <>
      <PageHeader
        title="Documents"
        description="Upload, organize and track the documents your AI assistant can use."
        actions={can("documents:upload") ? <UploadDialog /> : undefined}
      />
      <PageBody>
        <Card className="gap-0 overflow-hidden py-0">
          <div className="flex flex-col gap-3 border-b p-4 lg:flex-row lg:items-center">
            <div className="relative flex-1">
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="Search by title or file name…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="pl-9"
                aria-label="Search documents"
              />
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:flex">
              <Select value={status} onValueChange={(v) => setStatus(v as DocumentStatus | "all")}>
                <SelectTrigger className="w-full lg:w-36" aria-label="Status">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All statuses</SelectItem>
                  <SelectItem value="indexed">Indexed</SelectItem>
                  <SelectItem value="processing">Processing</SelectItem>
                  <SelectItem value="uploaded">Uploaded</SelectItem>
                  <SelectItem value="failed">Failed</SelectItem>
                </SelectContent>
              </Select>
              <Select value={fileType} onValueChange={(v) => setFileType(v as FileType | "all")}>
                <SelectTrigger className="w-full lg:w-32" aria-label="File type">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All types</SelectItem>
                  <SelectItem value="pdf">PDF</SelectItem>
                  <SelectItem value="docx">Word</SelectItem>
                  <SelectItem value="xlsx">Excel (xlsx)</SelectItem>
                  <SelectItem value="xls">Excel (xls)</SelectItem>
                </SelectContent>
              </Select>
              <Select value={category} onValueChange={setCategory}>
                <SelectTrigger className="col-span-2 w-full sm:col-span-1 lg:w-40" aria-label="Category">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All categories</SelectItem>
                  {categories?.map((c) => (
                    <SelectItem key={c.id} value={c.id}>
                      {c.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {isError ? (
            <div className="p-4">
              <ErrorState message={errorMessage(error)} onRetry={() => refetch()} />
            </div>
          ) : !isLoading && data?.items.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={FileText}
                title={filtered ? "No documents match these filters" : "No documents yet"}
                description={
                  filtered
                    ? "Try a different search or clear the filters."
                    : "Upload PDF, Word or Excel files. They are indexed automatically so you can ask questions about them."
                }
                action={!filtered && can("documents:upload") ? <UploadDialog /> : undefined}
              />
            </div>
          ) : (
            <DocumentTable documents={data?.items ?? []} categories={categories} loading={isLoading} />
          )}

          {data && data.total > PAGE_SIZE && (
            <div className="flex items-center justify-between border-t px-4 py-3 text-sm text-muted-foreground">
              <span>
                {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, data.total)} of {data.total}
              </span>
              <div className="flex gap-1">
                <Button variant="outline" size="icon-sm" disabled={page <= 1} onClick={() => setPage(page - 1)} aria-label="Previous page">
                  <ChevronLeft />
                </Button>
                <Button variant="outline" size="icon-sm" disabled={page >= pages} onClick={() => setPage(page + 1)} aria-label="Next page">
                  <ChevronRight />
                </Button>
              </div>
            </div>
          )}
        </Card>
      </PageBody>
    </>
  );
}
