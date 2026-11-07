"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import type { ConversationDetail, ConversationRead, Page } from "@/types";

export function useConversations(pageSize = 50) {
  return useQuery({
    queryKey: ["conversations", pageSize],
    queryFn: () => apiFetch<Page<ConversationRead>>(`chat/conversations?page_size=${pageSize}`),
  });
}

export function useConversation(id: string | null) {
  return useQuery({
    queryKey: ["conversation", id],
    queryFn: () => apiFetch<ConversationDetail>(`chat/conversations/${id}`),
    enabled: !!id,
  });
}

export function useCreateConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { title?: string; scope?: { document_ids?: string[] } } = {}) =>
      apiFetch<ConversationRead>("chat/conversations", { method: "POST", body }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });
}

export function useRenameConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      apiFetch<ConversationRead>(`chat/conversations/${id}`, { method: "PATCH", body: { title } }),
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      queryClient.invalidateQueries({ queryKey: ["conversation", id] });
    },
  });
}

export function useDeleteConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiFetch(`chat/conversations/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });
}
