// Evaluation metric catalog: labels, grouping and direction (mirrors backend runner LOWER_IS_BETTER).

export interface MetricDef {
  key: string;
  label: string;
  group: "Retrieval" | "Answer";
  lowerIsBetter?: boolean;
  description: string;
}

export const METRICS: MetricDef[] = [
  { key: "retrieval.precision@5", label: "Precision@5", group: "Retrieval", description: "Share of the top 5 passages that are relevant" },
  { key: "retrieval.recall@5", label: "Recall@5", group: "Retrieval", description: "Share of relevant passages found in the top 5" },
  { key: "retrieval.mrr", label: "MRR", group: "Retrieval", description: "1 / rank of the first relevant passage" },
  { key: "retrieval.ndcg@5", label: "NDCG@5", group: "Retrieval", description: "Rank-weighted relevance of the top 5" },
  { key: "retrieval.doc_recall@5", label: "Document recall@5", group: "Retrieval", description: "Relevant documents found in the top 5" },
  { key: "answer.faithfulness", label: "Faithfulness", group: "Answer", description: "Share of answer claims supported by the sources" },
  { key: "answer.hallucination_rate", label: "Hallucination rate", group: "Answer", lowerIsBetter: true, description: "Share of unsupported claims" },
  { key: "answer.answer_relevance", label: "Answer relevance", group: "Answer", description: "Does the answer address the question" },
  { key: "answer.context_relevance", label: "Context relevance", group: "Answer", description: "Share of retrieved passages useful for the question" },
  { key: "answer.correctness", label: "Correctness", group: "Answer", description: "Agreement with the reference answer" },
  { key: "answer.citation_precision", label: "Citation accuracy", group: "Answer", description: "Citations pointing at relevant passages" },
  { key: "answer.abstention_accuracy", label: "Abstention accuracy", group: "Answer", description: "Says “not found” exactly when it should" },
];

export const num = (m: Record<string, unknown> | null | undefined, key: string): number | null => {
  const v = m?.[key];
  return typeof v === "number" ? v : typeof v === "boolean" ? (v ? 1 : 0) : null;
};

/** Signed change where positive = better. */
export function improvement(def: MetricDef, current: number, baseline: number) {
  const d = current - baseline;
  return def.lowerIsBetter ? -d : d;
}
