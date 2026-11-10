"use client";

import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { ChatModelRead } from "@/types";

import { useSelectedModel } from "./models";

const PROVIDERS: Record<string, string> = { openai: "OpenAI", anthropic: "Anthropic", google: "Google" };

/** Price line under a model name: dollars per million input / output tokens. */
export function priceText(m: ChatModelRead): string {
  if (!m.price) return "price unknown";
  const n = (v: number) => (v >= 1 ? v.toFixed(2).replace(/\.?0+$/, "") : String(v));
  return `$${n(m.price.input)} in · $${n(m.price.output)} out per 1M tokens`;
}

export function ModelPicker({ disabled }: { disabled?: boolean }) {
  const { models, selected, select, isLoading } = useSelectedModel();
  if (isLoading || models.length === 0) return null;

  const groups = new Map<string, ChatModelRead[]>();
  for (const m of models) groups.set(m.provider, [...(groups.get(m.provider) ?? []), m]);

  return (
    <Select value={selected?.id} onValueChange={select} disabled={disabled}>
      <SelectTrigger
        size="sm"
        aria-label="Model"
        data-testid="model-picker"
        className="h-7 max-w-56 border-0 bg-transparent px-2 text-xs text-muted-foreground shadow-none hover:bg-muted"
      >
        <SelectValue>{selected?.label}</SelectValue>
      </SelectTrigger>
      <SelectContent align="start" side="top" className="min-w-72">
        {[...groups].map(([provider, items]) => (
          <SelectGroup key={provider}>
            <SelectLabel>{PROVIDERS[provider] ?? provider}</SelectLabel>
            {items.map((m) => (
              <SelectItem key={m.id} value={m.id} textValue={m.label} disabled={!!m.unavailable_reason}>
                <span className="flex flex-col items-start gap-0.5">
                  <span>
                    {m.label}
                    {m.is_default && <span className="text-muted-foreground"> · default</span>}
                  </span>
                  <span className="text-[11px] font-normal text-muted-foreground">
                    {m.unavailable_reason ? `Unavailable: ${m.unavailable_reason}` : priceText(m)}
                  </span>
                </span>
              </SelectItem>
            ))}
          </SelectGroup>
        ))}
      </SelectContent>
    </Select>
  );
}
