"use client";

import { useEffect, useRef } from "react";
import { toast } from "sonner";

import { IN_FLIGHT } from "@/components/common";
import type { DocumentRead, DocumentStatus } from "@/types";

/**
 * Tells the user when a document they were waiting for finishes, hits a temporary problem or fails.
 * Compares each refresh of the list with the previous one, so nothing is announced for documents that were
 * already in their final state when the page opened. Toast ids make a repeat of the same event a no-op.
 */
export function useStatusNotifications(documents: DocumentRead[] | undefined) {
  const previous = useRef(new Map<string, DocumentStatus>());

  useEffect(() => {
    if (!documents) return;
    const seen = previous.current;
    for (const d of documents) {
      const before = seen.get(d.id);
      seen.set(d.id, d.status);
      if (before === undefined || before === d.status) continue;
      const id = `${d.id}:${d.status}`;
      if (d.status === "indexed" && IN_FLIGHT.has(before)) {
        toast.success(`"${d.title}" is ready`, { id, description: "Processing completed. You can ask questions about it now." });
      } else if (d.status === "failed") {
        toast.error(`"${d.title}" could not be processed`, {
          id,
          description: d.error_message ?? "Use Retry in the document's menu to try again.",
        });
      } else if (d.status === "retrying") {
        toast.info(`"${d.title}" hit a temporary problem`, { id, description: "Trying again automatically." });
      }
    }
  }, [documents]);
}
