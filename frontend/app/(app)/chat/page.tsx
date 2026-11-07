"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { BrainCircuit } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Composer } from "@/features/chat/composer";
import { useCreateConversation } from "@/features/chat/queries";
import { Suggestions, useSuggestions } from "@/features/chat/suggestions";
import { errorMessage } from "@/lib/api";

export default function ChatHome() {
  const router = useRouter();
  const create = useCreateConversation();
  const { hasDocuments } = useSuggestions();

  async function start(question: string) {
    try {
      const conv = await create.mutateAsync({});
      router.push(`/chat/${conv.id}?q=${encodeURIComponent(question)}`);
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  return (
    <div className="flex flex-1 flex-col items-center justify-center overflow-y-auto p-6">
      <div className="w-full max-w-2xl space-y-6">
        <div className="text-center">
          <span className="mx-auto mb-4 grid size-12 place-items-center rounded-2xl bg-primary text-primary-foreground shadow">
            <BrainCircuit className="size-6" />
          </span>
          <h1 className="text-2xl font-semibold tracking-tight">Ask your documents</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Answers are based only on documents you can access, with citations to the exact source.
          </p>
        </div>
        <Composer onSend={start} disabled={create.isPending} autoFocus />
        {hasDocuments ? (
          <Suggestions onPick={start} />
        ) : (
          <p className="text-center text-sm text-muted-foreground">
            No indexed documents yet.{" "}
            <Button variant="link" className="h-auto p-0" asChild>
              <Link href="/documents">Upload some first</Link>
            </Button>
          </p>
        )}
      </div>
    </div>
  );
}
