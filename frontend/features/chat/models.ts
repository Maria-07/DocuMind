"use client";

import { useCallback, useSyncExternalStore } from "react";
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import type { ChatModelRead, ChatModelsResponse } from "@/types";

const STORAGE_KEY = "documind.chat.model";
const CHANGE_EVENT = "documind:chat-model";

// The chosen model is a per-browser convenience (like a theme), so it lives in localStorage. A
// useSyncExternalStore keeps every composer on the page in step and renders the server default
// until the browser value is known, so there is no hydration mismatch.
function subscribe(callback: () => void) {
  window.addEventListener("storage", callback);
  window.addEventListener(CHANGE_EVENT, callback);
  return () => {
    window.removeEventListener("storage", callback);
    window.removeEventListener(CHANGE_EVENT, callback);
  };
}

function readStored(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null; // private mode / blocked storage: fall back to the default model
  }
}

/** Models the user can pick (those whose provider key is configured), with prices. */
export function useChatModels() {
  return useQuery({
    queryKey: ["chat-models"],
    queryFn: () => apiFetch<ChatModelsResponse>("chat/models"),
    staleTime: 60_000,
  });
}

export function useSelectedModel() {
  const { data, isLoading } = useChatModels();
  const stored = useSyncExternalStore(subscribe, readStored, () => null);
  const models: ChatModelRead[] = data?.models ?? [];
  const selected = models.find((m) => m.id === stored) ?? models.find((m) => m.id === data?.default) ?? models[0];

  const select = useCallback((id: string) => {
    try {
      window.localStorage.setItem(STORAGE_KEY, id);
    } catch {
      /* storage unavailable: the choice then lasts only until the page reloads */
    }
    window.dispatchEvent(new Event(CHANGE_EVENT));
  }, []);

  return { models, selected, modelId: selected?.id, select, isLoading };
}
