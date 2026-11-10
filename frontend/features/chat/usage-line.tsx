"use client";

import { Coins } from "lucide-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatCount, formatUsd } from "@/lib/format";

import { useChatModels } from "./models";

/** One model call: the answer, or the small routing call that decides whether to search documents. */
export interface UsageStep {
  model?: string;
  input_tokens?: number;
  output_tokens?: number;
  reasoning_tokens?: number;
  cache_read_input_tokens?: number;
  cost_usd?: number | null;
}

/** Stored with every reply (see MessageRead.usage). Totals cover all calls made for the message. */
export interface UsageRecord extends UsageStep {
  routing?: UsageStep;
  answer?: UsageStep;
}

export function asUsage(value: unknown): UsageRecord | null {
  if (!value || typeof value !== "object") return null;
  const usage = value as UsageRecord;
  return (usage.input_tokens ?? 0) + (usage.output_tokens ?? 0) > 0 ? usage : null;
}

/** Total estimated cost of replies; replies whose model has no known price are ignored. */
export function totalCost(usages: (UsageRecord | null)[]): { usd: number; tokens: number; partial: boolean } {
  let usd = 0;
  let tokens = 0;
  let partial = false;
  for (const u of usages) {
    if (!u) continue;
    tokens += (u.input_tokens ?? 0) + (u.output_tokens ?? 0);
    if (typeof u.cost_usd === "number") usd += u.cost_usd;
    else partial = true;
  }
  return { usd, tokens, partial };
}

function StepRow({ title, step, labelOf }: { title: string; step: UsageStep; labelOf: (id?: string) => string }) {
  const extras = [
    step.reasoning_tokens ? `${formatCount(step.reasoning_tokens)} reasoning` : null,
    step.cache_read_input_tokens ? `${formatCount(step.cache_read_input_tokens)} cached` : null,
  ].filter(Boolean);
  return (
    <div>
      <div className="font-medium">
        {title} · {labelOf(step.model)}
      </div>
      <div className="text-background/80">
        {formatCount(step.input_tokens ?? 0)} in · {formatCount(step.output_tokens ?? 0)} out
        {extras.length > 0 && <> ({extras.join(", ")})</>} ·{" "}
        {typeof step.cost_usd === "number" ? formatUsd(step.cost_usd) : "price unknown"}
      </div>
    </div>
  );
}

export function UsageLine({
  usage,
  model,
  latencyMs,
}: {
  usage: UsageRecord | null;
  model?: string | null;
  latencyMs?: number | null;
}) {
  const { data } = useChatModels();
  const labelOf = (id?: string | null) => data?.models.find((m) => m.id === id)?.label ?? id ?? "model";
  if (!usage) return null;

  const cost = typeof usage.cost_usd === "number" ? formatUsd(usage.cost_usd) : null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          data-testid="usage-line"
          className="mt-2 flex w-fit items-center gap-1.5 rounded-md text-[11px] text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
        >
          <Coins className="size-3" aria-hidden />
          <span>{labelOf(model)}</span>
          <span aria-hidden>·</span>
          <span>
            {formatCount(usage.input_tokens ?? 0)} in · {formatCount(usage.output_tokens ?? 0)} out
          </span>
          <span aria-hidden>·</span>
          <span data-testid="usage-cost">{cost ?? "cost n/a"}</span>
        </button>
      </TooltipTrigger>
      <TooltipContent side="bottom" align="start" className="max-w-sm text-xs">
        {/* One block: the tooltip container lays its direct children out in a row. */}
        <div className="space-y-1.5">
          {usage.answer && <StepRow title="Answer" step={usage.answer} labelOf={labelOf} />}
          {usage.routing && <StepRow title="Routing" step={usage.routing} labelOf={labelOf} />}
          {latencyMs != null && <div className="text-background/80">Took {(latencyMs / 1000).toFixed(1)} s</div>}
          <div className="text-background/70">
            {cost
              ? "Estimated from the provider's published prices; check your invoice for exact charges."
              : "No price is known for this model, so only tokens are shown."}
          </div>
        </div>
      </TooltipContent>
    </Tooltip>
  );
}
