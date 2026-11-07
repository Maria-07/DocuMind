"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import type { CategoryRead, OrganizationRead, Page, Role, UserRead } from "@/types";

export function useUsers(q: string) {
  return useQuery({
    queryKey: ["users", q],
    queryFn: () => apiFetch<Page<UserRead>>(`users?page_size=100${q ? `&q=${encodeURIComponent(q)}` : ""}`),
  });
}

export function useCreateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { email: string; full_name: string; role: Role; password: string }) =>
      apiFetch<UserRead>("users", { method: "POST", body }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["users"] }),
  });
}

export function useUpdateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string; role?: Role; is_active?: boolean; full_name?: string }) =>
      apiFetch<UserRead>(`users/${id}`, { method: "PATCH", body }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["users"] }),
  });
}

export function useOrganization() {
  return useQuery({ queryKey: ["organization"], queryFn: () => apiFetch<OrganizationRead>("organization") });
}

export function useUpdateOrganization() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { name?: string; settings?: Partial<OrganizationRead["settings"]> }) =>
      apiFetch<OrganizationRead>("organization", { method: "PATCH", body }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["organization"] }),
  });
}

export function useCategoryMutations() {
  const queryClient = useQueryClient();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["categories"] });
  return {
    create: useMutation({
      mutationFn: (body: { name: string; color?: string }) => apiFetch<CategoryRead>("categories", { method: "POST", body }),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (id: string) => apiFetch(`categories/${id}`, { method: "DELETE" }),
      onSuccess: invalidate,
    }),
  };
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) =>
      apiFetch("auth/change-password", { method: "POST", body }),
  });
}
