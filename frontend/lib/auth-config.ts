"use client";

import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";

interface AuthConfig {
  registration_enabled: boolean;
}

/**
 * Whether public sign-up is open (the backend's REGISTRATION_ENABLED). `undefined` until known, and when the
 * API can't be reached: callers show the sign-up link only on `true`, so a closed demo never advertises it.
 */
export function useRegistrationOpen(): boolean | undefined {
  const { data } = useQuery({
    queryKey: ["auth-config"],
    queryFn: () => apiFetch<AuthConfig>("auth/config", { noRefresh: true }),
    staleTime: 5 * 60_000,
    retry: false,
  });
  return data?.registration_enabled;
}
