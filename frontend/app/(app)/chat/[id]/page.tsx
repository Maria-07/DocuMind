"use client";

import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef } from "react";
import { AlertTriangle, BrainCircuit, Loader2, Search, Sparkles, X } from "lucide-react";

import { ErrorState } from "@/components/common";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Answer, CitationList, GroundingBadge, SourcesPanel } from "@/features/chat/answer";
import { Composer } from "@/features/chat/composer";
import { useConversation } from "@/features/chat/queries";
import { Suggestions } from "@/features/chat/suggestions";
import { useChatStream } from "@/features/chat/use-chat-stream";
import { errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { initials } from "@/lib/format";

function UserBubble({ text }: { text: string }) {
  const { me } = useAuth();
  return (
    <div className="flex justify-end gap-3">
      <div className="max-w-[85%] rounded-2xl rounded-tr-sm bg-primary px-4 py-2.5 text-sm whitespace-pre-wrap text-primary-foreground">
        {text}
      </div>
      <Avatar className="size-8">
        <AvatarFallback className="text-xs">{initials(me?.user.full_name ?? "")}</AvatarFallback>
      </Avatar>
    </div>
  );
}

function AssistantBubble({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex gap-3">
      <span className="grid size-8 shrink-0 place-items-center rounded-full bg-sidebar text-white">
        <BrainCircuit className="size-4" />
      </span>
      <div className="min-w-0 max-w-[90%] flex-1 rounded-2xl rounded-tl-sm border bg-card px-4 py-3 shadow-xs">{children}</div>
    </div>
  );
}

function ChatThread() {
  const { id } = useParams<{ id: string }>();
  const params = useSearchParams();
  const router = useRouter();
  const { data, isLoading, isError, error } = useConversation(id);
  const { live, send, stop, dismiss, streaming } = useChatStream(id);
  const bottom = useRef<HTMLDivElement>(null);
  const autoSent = useRef(false);

  // A question passed from the start screen (?q=) is sent once, then removed from the URL.
  useEffect(() => {
    const q = params.get("q");
    if (q && !autoSent.current && data && data.messages.length === 0) {
      autoSent.current = true;
      router.replace(`/chat/${id}`);
      void send(q);
    }
  }, [params, data, id, router, send]);

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
  return (
    <>
      <header className="flex items-center gap-2 border-b bg-card px-4 py-3 sm:px-6">
        <h1 className="min-w-0 flex-1 truncate font-medium">{data?.title ?? " "}</h1>
        {data?.scope && Object.keys(data.scope).length > 0 && (
          <Badge variant="secondary">Scoped to {(data.scope.document_ids as string[] | undefined)?.length ?? 0} document(s)</Badge>
        )}
      </header>
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl space-y-6 px-4 py-6 sm:px-6" aria-live="polite">
          {isLoading && <Skeleton className="h-24 w-full" />}
          {!isLoading && messages.length === 0 && !live && (
            <div className="space-y-4 py-10 text-center">
              <Sparkles className="mx-auto size-8 text-primary" />
              <p className="text-sm text-muted-foreground">Ask anything about your documents to get started.</p>
              <Suggestions onPick={send} />
            </div>
          )}
          {messages.map((m) =>
            m.role === "user" ? (
              <UserBubble key={m.id} text={m.content} />
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
                {m.status === "complete" && <GroundingBadge grounded={m.grounded} />}
                {m.status === "cancelled" && <Badge variant="outline" className="mt-2">Stopped</Badge>}
              </AssistantBubble>
            ),
          )}
          {live && (
            <>
              <UserBubble text={live.question} />
              <AssistantBubble>
                {live.stage === "retrieving" && (
                  <p className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Search className="size-4 animate-pulse" /> Searching your documents…
                  </p>
                )}
                {live.stage === "generating" && !live.answer && (
                  <p className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="size-4 animate-spin" /> Reading {live.sources.length} passage
                    {live.sources.length === 1 ? "" : "s"} and writing an answer…
                  </p>
                )}
                {live.answer && <Answer content={live.answer} citations={live.citations} />}
                <CitationList citations={live.citations} />
                <SourcesPanel sources={live.sources} />
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
          <Composer onSend={send} onStop={stop} streaming={streaming} disabled={isLoading} autoFocus />
          <p className="mt-2 text-center text-[11px] text-muted-foreground">
            Answers cite your documents. Statements under “Interpretation” are AI analysis, not document facts.
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
