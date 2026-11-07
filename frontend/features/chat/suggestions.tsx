"use client";

import { Lightbulb } from "lucide-react";

import { useDocuments } from "@/features/documents/queries";

/** Suggested questions built from the user's own indexed documents (no canned examples). */
export function useSuggestions() {
  const { data } = useDocuments({ status: "indexed", page_size: 3, sort: "-created_at" });
  const docs = data?.items ?? [];
  const suggestions = docs.flatMap((d) =>
    d.file_type === "xlsx" || d.file_type === "xls"
      ? [`What are the most important figures in "${d.title}"?`]
      : [`What are the key points of "${d.title}"?`],
  );
  if (docs.length >= 2) suggestions.push(`What are the main differences between "${docs[0].title}" and "${docs[1].title}"?`);
  return { suggestions: suggestions.slice(0, 4), hasDocuments: docs.length > 0 };
}

export function Suggestions({ onPick }: { onPick: (q: string) => void }) {
  const { suggestions } = useSuggestions();
  if (!suggestions.length) return null;
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {suggestions.map((q) => (
        <button
          key={q}
          onClick={() => onPick(q)}
          className="flex items-start gap-2 rounded-xl border bg-card p-3 text-left text-sm transition-colors hover:border-primary/50 hover:bg-accent/40"
        >
          <Lightbulb className="mt-0.5 size-4 shrink-0 text-primary" />
          <span>{q}</span>
        </button>
      ))}
    </div>
  );
}
