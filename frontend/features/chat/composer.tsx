"use client";

import { useState } from "react";
import { ArrowUp, Square } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

import { ModelPicker } from "./model-picker";

export function Composer({
  onSend,
  onStop,
  streaming,
  disabled,
  autoFocus,
}: {
  onSend: (text: string) => void;
  onStop?: () => void;
  streaming?: boolean;
  disabled?: boolean;
  autoFocus?: boolean;
}) {
  const [text, setText] = useState("");
  const submit = () => {
    if (!text.trim() || streaming || disabled) return;
    onSend(text);
    setText("");
  };
  return (
    <form
      className="rounded-2xl border bg-card shadow-sm focus-within:ring-3 focus-within:ring-ring/30"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <Textarea
        value={text}
        autoFocus={autoFocus}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            submit();
          }
        }}
        placeholder="Ask a question about your documents…"
        aria-label="Message"
        maxLength={4000}
        rows={2}
        className="max-h-48 min-h-14 resize-none border-0 bg-transparent px-4 py-3 shadow-none focus-visible:ring-0"
      />
      <div className="flex items-center justify-between gap-2 px-2 pb-2">
        <ModelPicker disabled={streaming} />
        {streaming ? (
          <Button type="button" size="icon" variant="secondary" onClick={onStop} aria-label="Stop generating">
            <Square className="fill-current" />
          </Button>
        ) : (
          <Button type="submit" size="icon" disabled={!text.trim() || disabled} aria-label="Send">
            <ArrowUp />
          </Button>
        )}
      </div>
    </form>
  );
}
