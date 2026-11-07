"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import type { components } from "@/types/api";

type S = components["schemas"];
export type ComparisonRead = S["ComparisonRead"];
export type EvalDataset = S["EvalDatasetRead"];
export type EvalCase = S["EvalCaseRead"];
export type EvalCaseInput = S["EvalCaseInput"];
export type EvalRunSummary = S["EvalRunSummary"];
export type EvalRunDetail = S["EvalRunDetail"];
export type MetricPoint = S["MetricPoint"];
export type GroupRead = S["GroupRead"];
export type PermissionsRead = S["PermissionsRead"];
export type GrantInput = S["GrantInput"];

// --- analytics ------------------------------------------------------------------------------

export interface Overview {
  documents_total: number;
  documents_by_status: Record<string, number>;
  chunks_total: number;
  storage_bytes: number;
  conversations_total: number;
  questions_7d: number;
  grounded_rate_7d: number | null;
}
export interface DocumentAnalytics {
  days: string[];
  uploads: number[];
  indexed: number[];
  failed: number[];
  by_type: Record<string, number>;
  recent_activity: { action: string; at: string; document_id: string | null; title: string | null }[];
}
export interface RetrievalAnalytics {
  days: string[];
  queries: number[];
  no_answer: number[];
  avg_top_relevance: (number | null)[];
  retrieval_latency_ms: Record<"p50" | "p95" | "embed" | "search" | "postcheck" | "rerank", number | null>;
  queries_total: number;
  no_answer_rate: number | null;
  avg_filtered_by_postcheck: number | null;
  answer_latency_ms: { p50: number | null; p95: number | null };
  answers_total: number;
  grounded_rate: number | null;
  tokens: { input: number; output: number };
}

export const useOverview = () =>
  useQuery({ queryKey: ["analytics", "overview"], queryFn: () => apiFetch<Overview>("analytics/overview") });
export const useDocumentAnalytics = (days: number) =>
  useQuery({ queryKey: ["analytics", "documents", days], queryFn: () => apiFetch<DocumentAnalytics>(`analytics/documents?days=${days}`) });
export const useRetrievalAnalytics = (days: number) =>
  useQuery({ queryKey: ["analytics", "retrieval", days], queryFn: () => apiFetch<RetrievalAnalytics>(`analytics/retrieval?days=${days}`) });

// --- comparisons ----------------------------------------------------------------------------

export const useComparisons = () =>
  useQuery({ queryKey: ["comparisons"], queryFn: () => apiFetch<ComparisonRead[]>("comparisons") });

export function useCreateComparison() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { document_ids: string[]; question?: string }) =>
      apiFetch<ComparisonRead>("comparisons", { method: "POST", body }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["comparisons"] }),
  });
}

// --- evaluations ----------------------------------------------------------------------------

export const useDatasets = () =>
  useQuery({ queryKey: ["eval", "datasets"], queryFn: () => apiFetch<EvalDataset[]>("evaluations/datasets") });
export const useCases = (datasetId: string) =>
  useQuery({ queryKey: ["eval", "cases", datasetId], queryFn: () => apiFetch<EvalCase[]>(`evaluations/datasets/${datasetId}/cases`) });
export const useRuns = (datasetId: string) =>
  useQuery({
    queryKey: ["eval", "runs", datasetId],
    queryFn: () => apiFetch<EvalRunSummary[]>(`evaluations/results?dataset_id=${datasetId}`),
    refetchInterval: (q) => (q.state.data?.some((r) => r.status === "queued" || r.status === "running") ? 3000 : false),
  });
export const useRun = (runId: string) =>
  useQuery({
    queryKey: ["eval", "run", runId],
    queryFn: () => apiFetch<EvalRunDetail>(`evaluations/results/${runId}`),
    refetchInterval: (q) => (q.state.data && ["queued", "running"].includes(q.state.data.status) ? 3000 : false),
  });
/** ``completedRuns`` is part of the key so the trend refetches when a polled run finishes. */
export const useMetricHistory = (datasetId: string, completedRuns: number) =>
  useQuery({
    queryKey: ["eval", "metrics", datasetId, completedRuns],
    queryFn: () => apiFetch<MetricPoint[]>(`evaluations/metrics?dataset_id=${datasetId}`),
    placeholderData: (prev) => prev,
  });

export function useEvalMutations(datasetId?: string) {
  const queryClient = useQueryClient();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["eval"] });
  return {
    createDataset: useMutation({
      mutationFn: (body: { name: string; description?: string }) =>
        apiFetch<EvalDataset>("evaluations/datasets", { method: "POST", body }),
      onSuccess: invalidate,
    }),
    deleteDataset: useMutation({
      mutationFn: (id: string) => apiFetch(`evaluations/datasets/${id}`, { method: "DELETE" }),
      onSuccess: invalidate,
    }),
    addCases: useMutation({
      mutationFn: (cases: EvalCaseInput[]) =>
        apiFetch<EvalCase[]>(`evaluations/datasets/${datasetId}/cases`, { method: "POST", body: cases }),
      onSuccess: invalidate,
    }),
    deleteCase: useMutation({
      mutationFn: (caseId: string) => apiFetch(`evaluations/datasets/${datasetId}/cases/${caseId}`, { method: "DELETE" }),
      onSuccess: invalidate,
    }),
    run: useMutation({
      mutationFn: () => apiFetch<EvalRunSummary>("evaluations/run", { method: "POST", body: { dataset_id: datasetId } }),
      onSuccess: invalidate,
    }),
  };
}

// --- embeddings -----------------------------------------------------------------------------

export interface EmbeddingConfig {
  id: string;
  name: string;
  is_active: boolean;
  embedding_provider: string;
  embedding_model: string;
  dim: number;
  sparse_model: string;
  chunk_size: number;
  chunk_overlap: number;
  collection: string;
  documents: number;
  chunks: number;
  points: number | null;
  created_at: string;
}
export interface EmbeddingOverview {
  configs: EmbeddingConfig[];
  documents_total: number;
  documents_on_active: number;
  documents_stale: number;
  documents_not_indexed: number;
  documents_failed: number;
}

export const useEmbeddings = () =>
  useQuery({ queryKey: ["embeddings"], queryFn: () => apiFetch<EmbeddingOverview>("embeddings") });

export function useReindex() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (mode: "stale" | "failed" | "all") =>
      apiFetch<{ queued: number }>("embeddings/reindex", { method: "POST", body: { mode } }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["embeddings"] });
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}

// --- sharing & groups -----------------------------------------------------------------------

export const useGroups = () => useQuery({ queryKey: ["groups"], queryFn: () => apiFetch<GroupRead[]>("groups") });

export function useGroupMutations() {
  const queryClient = useQueryClient();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["groups"] });
  return {
    create: useMutation({
      mutationFn: (body: { name: string; description?: string }) => apiFetch<GroupRead>("groups", { method: "POST", body }),
      onSuccess: invalidate,
    }),
    setMembers: useMutation({
      mutationFn: ({ id, user_ids }: { id: string; user_ids: string[] }) =>
        apiFetch<GroupRead>(`groups/${id}/members`, { method: "PUT", body: { user_ids } }),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (id: string) => apiFetch(`groups/${id}`, { method: "DELETE" }),
      onSuccess: invalidate,
    }),
  };
}

export const usePermissions = (documentId: string, enabled: boolean) =>
  useQuery({
    queryKey: ["document", documentId, "permissions"],
    queryFn: () => apiFetch<PermissionsRead>(`documents/${documentId}/permissions`),
    enabled,
  });

export function useSetPermissions(documentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (grants: GrantInput[]) =>
      apiFetch<PermissionsRead>(`documents/${documentId}/permissions`, { method: "PUT", body: { grants } }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["document", documentId] });
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}

export interface Member {
  id: string;
  full_name: string;
  email: string;
}
export const useMembers = (enabled = true) =>
  useQuery({ queryKey: ["members"], queryFn: () => apiFetch<Member[]>("organization/members"), enabled, staleTime: 60_000 });
