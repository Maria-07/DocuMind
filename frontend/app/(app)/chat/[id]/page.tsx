"use client";

import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { AlertTriangle, BrainCircuit, Loader2, Pencil, Search, Sparkles, X } from "lucide-react";

import { ErrorState } from "@/components/common";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { Answer, CitationList, GroundingBadge, SourcesPanel } from "@/features/chat/answer";
import { Composer } from "@/features/chat/composer";
import { useSelectedModel } from "@/features/chat/models";
import { useConversation } from "@/features/chat/queries";
import { Suggestions } from "@/features/chat/suggestions";
import { asUsage, totalCost, UsageLine } from "@/features/chat/usage-line";
import { useChatStream } from "@/features/chat/use-chat-stream";
import { errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatCount, formatUsd, initials } from "@/lib/format";

function UserAvatar() {
  const { me } = useAuth();
  return (
    <Avatar className="size-8">
      <AvatarFallback className="text-xs">{initials(me?.user.full_name ?? "")}</AvatarFallback>
    </Avatar>
  );
}

function UserBubble({
  text,
  edited,
  onEdit,
}: {
  text: string;
  edited?: boolean;
  /** Present for a saved message the user may edit; omitted while a reply is streaming. */
  onEdit?: () => void;
}) {
  return (
    <div className="group flex items-start justify-end gap-3" data-testid="user-message">
      {onEdit && (
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={onEdit}
          aria-label="Edit message"
          className="mt-1.5 text-muted-foreground opacity-100 sm:opacity-0 sm:group-focus-within:opacity-100 sm:group-hover:opacity-100"
        >
          <Pencil />
        </Button>
      )}
      <div className="flex max-w-[85%] flex-col items-end gap-0.5">
        <div className="rounded-2xl rounded-tr-sm bg-primary px-4 py-2.5 text-sm whitespace-pre-wrap text-primary-foreground">
          {text}
        </div>
        {edited && <span className="text-[11px] text-muted-foreground">edited</span>}
      </div>
      <UserAvatar />
    </div>
  );
}

function EditBox({ initial, onSubmit, onCancel }: { initial: string; onSubmit: (text: string) => void; onCancel: () => void }) {
  const [text, setText] = useState(initial);
  const submit = () => text.trim() && onSubmit(text);
  return (
    <div className="flex justify-end gap-3">
      <div className="w-full max-w-[85%] space-y-2 rounded-2xl border bg-card p-3 shadow-sm" data-testid="edit-box">
        <Textarea
          autoFocus
          aria-label="Edit your message"
          value={text}
          maxLength={4000}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") onCancel();
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
          }}
          className="min-h-16 resize-none"
        />
        <p className="text-[11px] text-muted-foreground">
          This replaces your message and the replies after it, and answers again with the model selected below. Ctrl+Enter to
          resubmit, Esc to cancel.
        </p>
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onCancel}>
            Cancel
          </Button>
          <Button size="sm" onClick={submit} disabled={!text.trim()}>
            Resubmit
          </Button>
        </div>
      </div>
      <UserAvatar />
    </div>
  );
}

function AssistantBubble({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex gap-3">
      <span className="grid size-8 shrink-0 place-items-center rounded-full bg-sidebar text-white">
        <BrainCircuit className="size-4" />
      </span>
      <div data-testid="assistant-message" className="min-w-0 max-w-[90%] flex-1 rounded-2xl rounded-tl-sm border bg-card px-4 py-3 shadow-xs">
        {children}
      </div>
    </div>
  );
}

function ChatThread() {
  const { id } = useParams<{ id: string }>();
  const params = useSearchParams();
  const router = useRouter();
  const { data, isLoading, isError, error } = useConversation(id);
  const { live, send, stop, dismiss, streaming } = useChatStream(id);
  const { modelId } = useSelectedModel();
  const [editing, setEditing] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const autoSent = useRef(false);

  // A question passed from the start screen (?q=) is sent once, then removed from the URL. It waits
  // for the model list so the model chosen on the start screen is the one that answers.
  useEffect(() => {
    const q = params.get("q");
    if (q && !autoSent.current && data && data.messages.length === 0 && modelId) {
      autoSent.current = true;
      router.replace(`/chat/${id}`);
      void send(q, { modelId });
    }
  }, [params, data, id, router, send, modelId]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [data?.messages.length, live?.answer, live?.stage]);

  if (isError) {
    return (
      <div className="p-6">
        <ErrorState message={errorMessage(error)} />
      </div>
    );
  }

  const messages = data?.messages ?? [];
  // While an edited turn streams, the messages it replaces are hidden (the server has already superseded them).
  let cut = live?.editFrom ? messages.findIndex((m) => m.id === live.editFrom) : -1;
  if (cut < 0 && live?.stage === "error") {
    // A failed turn is saved too, but the live bubble carries the real error: show it once, not twice.
    const lastUser = messages.map((m) => m.role).lastIndexOf("user");
    if (lastUser >= 0 && messages[lastUser].content === live.question) cut = lastUser;
  }
  const shown = cut >= 0 ? messages.slice(0, cut) : messages;
  const cost = totalCost(shown.filter((m) => m.role === "assistant").map((m) => asUsage(m.usage)));

  const resubmit = (messageId: string, text: string) => {
    setEditing(null);
    void send(text, { modelId, editMessageId: messageId });
  };

  return (
    <>
      <header className="flex items-center gap-2 border-b bg-card px-4 py-3 sm:px-6">
        <h1 className="min-w-0 flex-1 truncate font-medium">{data?.title ?? " "}</h1>
        {cost.tokens > 0 && (
          <span
            data-testid="conversation-cost"
            className="shrink-0 text-xs text-muted-foreground"
            title={
              cost.partial
                ? "Estimated from published prices. Some replies used a model with no known price and are not included in the cost."
                : "Estimated from published prices"
            }
          >
            {cost.usd > 0 || !cost.partial ? `${cost.partial ? "≥ " : ""}${formatUsd(cost.usd)} · ` : ""}
            {formatCount(cost.tokens)} tokens
          </span>
        )}
        {data?.scope && Object.keys(data.scope).length > 0 && (
          <Badge variant="secondary">Scoped to {(data.scope.document_ids as string[] | undefined)?.length ?? 0} document(s)</Badge>
        )}
      </header>
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl space-y-6 px-4 py-6 sm:px-6" aria-live="polite">
          {isLoading && <Skeleton className="h-24 w-full" />}
          {!isLoading && shown.length === 0 && !live && (
            <div className="space-y-4 py-10 text-center">
              <Sparkles className="mx-auto size-8 text-primary" />
              <p className="text-sm text-muted-foreground">Say hello, or ask anything about your documents.</p>
              <Suggestions onPick={(q) => send(q, { modelId })} />
            </div>
          )}
          {shown.map((m) =>
            m.role === "user" ? (
              editing === m.id ? (
                <EditBox key={m.id} initial={m.content} onSubmit={(text) => resubmit(m.id, text)} onCancel={() => setEditing(null)} />
              ) : (
                <UserBubble
                  key={m.id}
                  text={m.content}
                  edited={!!m.edit_of}
                  onEdit={streaming ? undefined : () => setEditing(m.id)}
                />
              )
            ) : (
              <AssistantBubble key={m.id}>
                {m.status === "error" && !m.content ? (
                  <p className="flex items-center gap-2 text-sm text-destructive">
                    <AlertTriangle className="size-4" /> This answer could not be completed.
                  </p>
                ) : (
                  <Answer content={m.content} citations={m.citations ?? []} />
                )}
                <CitationList citations={m.citations ?? []} />
                {m.status === "complete" && m.route !== "chat" && <GroundingBadge grounded={m.grounded} />}
                {m.status === "cancelled" && <Badge variant="outline" className="mt-2">Stopped</Badge>}
                <UsageLine usage={asUsage(m.usage)} model={m.model} latencyMs={m.latency_ms} />
              </AssistantBubble>
            ),
          )}
          {live && (
            <>
              <UserBubble text={live.question} edited={!!live.editFrom} />
              <AssistantBubble>
                {live.stage === "retrieving" && (
                  <p className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Search className="size-4 animate-pulse" /> Searching your documents…
                  </p>
                )}
                {live.stage === "generating" && !live.answer && live.sources.length > 0 && (
                  <p className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="size-4 animate-spin" /> Reading {live.sources.length} passage
                    {live.sources.length === 1 ? "" : "s"} and writing an answer…
                  </p>
                )}
                {/* Deciding how to answer, or writing a reply that needs no documents. */}
                {!live.answer && (live.stage === "thinking" || (live.stage === "generating" && live.sources.length === 0)) && (
                  <p className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="size-4 animate-spin" /> Thinking…
                  </p>
                )}
                {live.answer && <Answer content={live.answer} citations={live.citations} />}
                <CitationList citations={live.citations} />
                <SourcesPanel sources={live.sources} />
                <UsageLine usage={live.usage ?? null} model={live.model} latencyMs={live.latencyMs} />
                {live.stage === "error" && (
                  <div className="mt-2 flex items-center gap-2 text-sm text-destructive">
                    <AlertTriangle className="size-4" /> <span className="flex-1">{live.error}</span>
                    <Button variant="ghost" size="icon-xs" onClick={dismiss} aria-label="Dismiss">
                      <X />
                    </Button>
                  </div>
                )}
              </AssistantBubble>
            </>
          )}
          <div ref={bottom} />
        </div>
      </div>
      <div className="border-t bg-background/80 p-4 backdrop-blur">
        <div className="mx-auto max-w-3xl">
          <Composer
            onSend={(text) => send(text, { modelId })}
            onStop={stop}
            streaming={streaming}
            disabled={isLoading}
            autoFocus
          />
          <p className="mt-2 text-center text-[11px] text-muted-foreground">
            Answers about your documents cite their sources. Statements under “Interpretation” are AI analysis, not document facts.
            Costs are estimates.
          </p>
        </div>
      </div>
    </>
  );
}

export default function ChatPage() {
  return (
    <Suspense>
      <ChatThread />
    </Suspense>
  );
}
