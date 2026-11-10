"use client";

import { useCallback } from "react";
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

export interface UploadFileInput {
  file: File;
  visibility?: DocumentVisibility;
  categoryId?: string;
  onProgress: (fraction: number) => void;
}

/**
 * Uploads ONE file per request. The dialog runs several in parallel, so a large file never holds
 * back the others, and each document appears in the list as soon as its own upload is stored.
 */
export function useUploadDocument() {
  const queryClient = useQueryClient();
  return useCallback(
    async ({ file, visibility, categoryId, onProgress }: UploadFileInput) => {
      const form = new FormData();
      form.append("files", file, file.name);
      if (visibility) form.append("visibility", visibility);
      if (categoryId) form.append("category_id", categoryId);
      try {
        return await uploadWithProgress<UploadResponse>("documents/upload", form, onProgress);
      } finally {
        void queryClient.invalidateQueries({ queryKey: ["documents"] });
      }
    },
    [queryClient],
  );
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
