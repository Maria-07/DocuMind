"use client";

import { useCallback, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { ApiError, errorMessage } from "@/lib/api";
import { postSse } from "@/lib/sse";
import type { CitationRead, SourceRead } from "@/types";

import { asUsage, type UsageRecord } from "./usage-line";

export interface SendOptions {
  /** Model id from GET /chat/models; omitted = the server's default. */
  modelId?: string;
  /** Edit and resubmit: replaces this earlier user message and everything after it. */
  editMessageId?: string;
}

// "thinking": the server is deciding whether the message needs a document search (a greeting does not).
export type Stage = "idle" | "thinking" | "retrieving" | "generating" | "done" | "error";

export interface LiveTurn {
  question: string;
  answer: string;
  sources: SourceRead[];
  citations: CitationRead[];
  stage: Stage;
  grounded?: boolean;
  /** "chat" (conversational reply, no documents) or "documents" (retrieval + citations). */
  route?: string;
  /** The model answering (from the first event) and, when finished, what the reply cost. */
  model?: string;
  usage?: UsageRecord | null;
  latencyMs?: number;
  /** Set when editing: the persisted messages from this one onward are hidden while the new turn streams. */
  editFrom?: string;
  error?: string;
}

/** Sends one question and streams the answer (SSE). Persisted history is re-fetched when done. */
export function useChatStream(conversationId: string) {
  const queryClient = useQueryClient();
  const [live, setLive] = useState<LiveTurn | null>(null);
  const controller = useRef<AbortController | null>(null);

  const send = useCallback(
    async (question: string, options: SendOptions = {}) => {
      const text = question.trim();
      if (!text || controller.current) return;
      const abort = new AbortController();
      controller.current = abort;
      setLive({
        question: text,
        answer: "",
        sources: [],
        citations: [],
        stage: "thinking",
        model: options.modelId,
        editFrom: options.editMessageId,
      });
      const patch = (p: Partial<LiveTurn> | ((t: LiveTurn) => Partial<LiveTurn>)) =>
        setLive((t) => (t ? { ...t, ...(typeof p === "function" ? p(t) : p) } : t));

      try {
        for await (const { event, data } of postSse(
          `chat/conversations/${conversationId}/messages`,
          {
            content: text,
            ...(options.modelId ? { model: options.modelId } : {}),
            ...(options.editMessageId ? { edit_message_id: options.editMessageId } : {}),
          },
          abort.signal,
        )) {
          const d = data as Record<string, unknown>;
          switch (event) {
            case "meta":
              patch({ model: d.model as string | undefined });
              break;
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
                route: d.route as string | undefined,
                usage: asUsage(d.usage),
                latencyMs: d.latency_ms as number | undefined,
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
          queryClient.invalidateQueries({ queryKey: ["chat-models"] }), // a quota error greys a model out
        ]);
        // The persisted message replaces the live one (unless there was an error to show).
        setLive((t) => (t && t.stage === "error" ? t : null));
      }
    },
    [conversationId, queryClient],
  );

  const stop = useCallback(() => controller.current?.abort(), []);
  const dismiss = useCallback(() => setLive(null), []);

  return { live, send, stop, dismiss, streaming: live !== null && (live.stage === "thinking" || live.stage === "retrieving" || live.stage === "generating") };
}
