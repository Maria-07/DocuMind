"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { MessageSquarePlus, MoreHorizontal, Pencil, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { timeAgo } from "@/lib/format";
import { cn } from "@/lib/utils";

import { DeleteConversationDialog, RenameConversationDialog } from "./conversation-dialogs";
import { useConversations, useCreateConversation } from "./queries";

type Dialogs = { kind: "rename" | "delete"; id: string; title: string } | null;

export function ConversationList() {
  const { id: activeId } = useParams<{ id?: string }>();
  const router = useRouter();
  const { data, isLoading } = useConversations();
  const create = useCreateConversation();
  const [dialog, setDialog] = useState<Dialogs>(null);
  const close = () => setDialog(null);

  return (
    <div className="flex h-full flex-col">
      <div className="p-3">
        <Button
          className="w-full"
          disabled={create.isPending}
          onClick={async () => {
            const conv = await create.mutateAsync({});
            router.push(`/chat/${conv.id}`);
          }}
        >
          <MessageSquarePlus /> New conversation
        </Button>
      </div>
      <div className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
        {isLoading && Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="mx-1 my-1 h-10" />)}
        {data?.items.length === 0 && <p className="px-3 py-4 text-sm text-muted-foreground">No conversations yet.</p>}
        {data?.items.map((c) => (
          <div
            key={c.id}
            className={cn(
              "group flex items-center rounded-lg pr-1 transition-colors",
              c.id === activeId ? "bg-accent text-accent-foreground" : "hover:bg-muted",
            )}
          >
            <Link href={`/chat/${c.id}`} className="min-w-0 flex-1 px-3 py-2">
              <span className="block truncate text-sm font-medium">{c.title}</span>
              <span className="block text-xs text-muted-foreground">{timeAgo(c.updated_at)}</span>
            </Link>
            {/* Not modal: the dialogs it opens manage focus and scroll locking themselves. */}
            <DropdownMenu modal={false}>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon-xs" className="opacity-0 group-hover:opacity-100 focus:opacity-100" aria-label={`Options for ${c.title}`}>
                  <MoreHorizontal />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onSelect={() => setDialog({ kind: "rename", id: c.id, title: c.title })}>
                  <Pencil /> Rename
                </DropdownMenuItem>
                <DropdownMenuItem
                  variant="destructive"
                  onSelect={() => setDialog({ kind: "delete", id: c.id, title: c.title })}
                >
                  <Trash2 /> Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        ))}
      </div>
      <RenameConversationDialog conversation={dialog?.kind === "rename" ? dialog : null} onClose={close} />
      <DeleteConversationDialog
        conversation={dialog?.kind === "delete" ? dialog : null}
        onClose={close}
        onDeleted={(id) => {
          if (id === activeId) router.replace("/chat");
        }}
      />
    </div>
  );
}
