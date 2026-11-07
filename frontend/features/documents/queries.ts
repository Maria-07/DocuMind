"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, uploadWithProgress } from "@/lib/api";
import type {
  CategoryRead,
  ChunkPage,
  DocumentDetail,
  DocumentRead,
  DocumentStatus,
  DocumentVisibility,
  FileType,
  IngestionJobRead,
  Page,
  UploadResponse,
} from "@/types";

export interface DocumentFilters {
  q?: string;
  status?: DocumentStatus;
  file_type?: FileType;
  category_id?: string;
  sort?: string;
  page?: number;
  page_size?: number;
}

function qs(params: Record<string, string | number | undefined>) {
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") search.set(k, String(v));
  const s = search.toString();
  return s ? `?${s}` : "";
}

const inFlight = (docs: DocumentRead[]) => docs.some((d) => d.status === "uploaded" || d.status === "processing");

export function useDocuments(filters: DocumentFilters) {
  return useQuery({
    queryKey: ["documents", filters],
    queryFn: () => apiFetch<Page<DocumentRead>>(`documents${qs({ ...filters })}`),
    placeholderData: keepPreviousData,
    // Poll while anything is still being ingested so status badges update live.
    refetchInterval: (query) => (query.state.data && inFlight(query.state.data.items) ? 3000 : false),
  });
}

export function useDocumentCount(status?: DocumentStatus) {
  return useQuery({
    queryKey: ["documents", "count", status ?? "all"],
    queryFn: async () => (await apiFetch<Page<DocumentRead>>(`documents${qs({ status, page_size: 1 })}`)).total,
  });
}

export function useDocument(id: string) {
  return useQuery({
    queryKey: ["document", id],
    queryFn: () => apiFetch<DocumentDetail>(`documents/${id}`),
    refetchInterval: (query) =>
      query.state.data && (query.state.data.status === "uploaded" || query.state.data.status === "processing")
        ? 3000
        : false,
  });
}

export function useDocumentJobs(id: string) {
  return useQuery({
    queryKey: ["document", id, "jobs"],
    queryFn: () => apiFetch<IngestionJobRead[]>(`documents/${id}/jobs`),
  });
}

export function useDocumentChunks(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["document", id, "chunks"],
    queryFn: () => apiFetch<ChunkPage>(`documents/${id}/chunks?page_size=100`),
    enabled,
  });
}

export function useCategories() {
  return useQuery({ queryKey: ["categories"], queryFn: () => apiFetch<CategoryRead[]>("categories") });
}

export function useUploadDocuments() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      files,
      visibility,
      categoryId,
      onProgress,
    }: {
      files: File[];
      visibility?: DocumentVisibility;
      categoryId?: string;
      onProgress: (fraction: number) => void;
    }) => {
      const form = new FormData();
      for (const f of files) form.append("files", f, f.name);
      if (visibility) form.append("visibility", visibility);
      if (categoryId) form.append("category_id", categoryId);
      return uploadWithProgress<UploadResponse>("documents/upload", form, onProgress);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["documents"] }),
  });
}

function useDocumentMutation<V>(fn: (vars: V) => Promise<unknown>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      queryClient.invalidateQueries({ queryKey: ["document"] });
    },
  });
}

export const useDeleteDocument = () =>
  useDocumentMutation((id: string) => apiFetch(`documents/${id}`, { method: "DELETE" }));

export const useReprocessDocument = () =>
  useDocumentMutation((id: string) => apiFetch(`documents/${id}/reprocess`, { method: "POST" }));

export const useUpdateDocument = () =>
  useDocumentMutation(
    ({ id, ...body }: { id: string; title?: string; visibility?: DocumentVisibility; category_id?: string | null }) =>
      apiFetch(`documents/${id}`, { method: "PATCH", body }),
  );
