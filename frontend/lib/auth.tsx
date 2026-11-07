"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { apiFetch, refreshAccessToken, setAccessToken, setSessionExpiredHandler } from "@/lib/api";
import type { MeResponse, Permission, TokenResponse } from "@/types";

type Status = "loading" | "authenticated" | "anonymous";

interface AuthContextValue {
  status: Status;
  me: MeResponse | null;
  can: (permission: Permission) => boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (data: { email: string; password: string; full_name: string; organization_name: string }) => Promise<void>;
  logout: () => Promise<void>;
  reload: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<Status>("loading");
  const [me, setMe] = useState<MeResponse | null>(null);

  const loadMe = useCallback(async () => {
    const data = await apiFetch<MeResponse>("auth/me");
    setMe(data);
    setStatus("authenticated");
  }, []);

  const clear = useCallback(() => {
    setAccessToken(null);
    setMe(null);
    setStatus("anonymous");
    queryClient.clear(); // never show one user's cached data to the next
  }, [queryClient]);

  // Restore the session from the httpOnly refresh cookie on first load.
  useEffect(() => {
    setSessionExpiredHandler(clear);
    let cancelled = false;
    (async () => {
      const token = await refreshAccessToken();
      if (cancelled) return;
      if (!token) return setStatus("anonymous");
      try {
        await loadMe();
      } catch {
        if (!cancelled) clear();
      }
    })();
    return () => {
      cancelled = true;
      setSessionExpiredHandler(null);
    };
  }, [clear, loadMe]);

  const login = useCallback(
    async (email: string, password: string) => {
      const res = await apiFetch<TokenResponse>("auth/login", { method: "POST", body: { email, password }, noRefresh: true });
      setAccessToken(res.access_token);
      await loadMe();
    },
    [loadMe],
  );

  const register = useCallback<AuthContextValue["register"]>(
    async (data) => {
      const res = await apiFetch<TokenResponse>("auth/register", { method: "POST", body: data, noRefresh: true });
      setAccessToken(res.access_token);
      await loadMe();
    },
    [loadMe],
  );

  const logout = useCallback(async () => {
    try {
      await apiFetch("auth/logout", { method: "POST", noRefresh: true });
    } finally {
      clear();
    }
  }, [clear]);

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      me,
      can: (permission) => !!me?.permissions.includes(permission),
      login,
      register,
      logout,
      reload: loadMe,
    }),
    [status, me, login, register, logout, loadMe],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
