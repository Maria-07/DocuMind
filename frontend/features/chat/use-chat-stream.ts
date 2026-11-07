"use client";

import { useCallback, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { ApiError, errorMessage } from "@/lib/api";
import { postSse } from "@/lib/sse";
import type { CitationRead, SourceRead } from "@/types";

export type Stage = "idle" | "retrieving" | "generating" | "done" | "error";

export interface LiveTurn {
  question: string;
  answer: string;
  sources: SourceRead[];
  citations: CitationRead[];
  stage: Stage;
  grounded?: boolean;
  error?: string;
}

/** Sends one question and streams the answer (SSE). Persisted history is re-fetched when done. */
export function useChatStream(conversationId: string) {
  const queryClient = useQueryClient();
  const [live, setLive] = useState<LiveTurn | null>(null);
  const controller = useRef<AbortController | null>(null);

  const send = useCallback(
    async (question: string) => {
      const text = question.trim();
      if (!text || controller.current) return;
      const abort = new AbortController();
      controller.current = abort;
      setLive({ question: text, answer: "", sources: [], citations: [], stage: "retrieving" });
      const patch = (p: Partial<LiveTurn> | ((t: LiveTurn) => Partial<LiveTurn>)) =>
        setLive((t) => (t ? { ...t, ...(typeof p === "function" ? p(t) : p) } : t));

      try {
        for await (const { event, data } of postSse(
          `chat/conversations/${conversationId}/messages`,
          { content: text },
          abort.signal,
        )) {
          const d = data as Record<string, unknown>;
          switch (event) {
            case "status":
              patch({ stage: d.stage as Stage });
              break;
            case "sources":
              patch({ sources: d.sources as SourceRead[] });
              break;
            case "token":
              patch((t) => ({ answer: t.answer + (d.text as string) }));
              break;
            case "citation":
              patch((t) => ({ citations: [...t.citations, d as unknown as CitationRead] }));
              break;
            case "error":
              patch({ stage: "error", error: d.message as string });
              break;
            case "done":
              patch((t) => ({
                stage: t.stage === "error" ? "error" : "done",
                grounded: d.grounded as boolean,
                citations: d.citations as CitationRead[],
              }));
              break;
          }
        }
      } catch (err) {
        if (abort.signal.aborted) {
          patch({ stage: "done" });
        } else {
          const message = err instanceof ApiError && err.status === 429 ? "You're sending messages too quickly. Please wait a moment." : errorMessage(err);
          patch({ stage: "error", error: message });
        }
      } finally {
        controller.current = null;
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ["conversation", conversationId] }),
          queryClient.invalidateQueries({ queryKey: ["conversations"] }),
        ]);
        // The persisted message replaces the live one (unless there was an error to show).
        setLive((t) => (t && t.stage === "error" ? t : null));
      }
    },
    [conversationId, queryClient],
  );

  const stop = useCallback(() => controller.current?.abort(), []);
  const dismiss = useCallback(() => setLive(null), []);

  return { live, send, stop, dismiss, streaming: live !== null && (live.stage === "retrieving" || live.stage === "generating") };
}
