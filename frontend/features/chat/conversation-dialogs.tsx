"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { errorMessage } from "@/lib/api";

import { useDeleteConversation, useRenameConversation } from "./queries";

const MAX_TITLE = 200; // the API's limit

interface Target {
  id: string;
  title: string;
}

/** Rename a conversation. Rendered while ``conversation`` is set; ``onClose`` clears it. */
export function RenameConversationDialog({ conversation, onClose }: { conversation: Target | null; onClose: () => void }) {
  return (
    <Dialog open={conversation !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        {/* Keyed so the field starts from this conversation's title each time it opens. */}
        {conversation && <RenameForm key={conversation.id} conversation={conversation} onClose={onClose} />}
      </DialogContent>
    </Dialog>
  );
}

function RenameForm({ conversation, onClose }: { conversation: Target; onClose: () => void }) {
  const rename = useRenameConversation();
  const [title, setTitle] = useState(conversation.title);
  const trimmed = title.trim();
  const unchanged = trimmed === conversation.title;

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!trimmed || unchanged) return onClose();
    rename.mutate(
      { id: conversation.id, title: trimmed },
      {
        onSuccess: () => {
          toast.success("Conversation renamed");
          onClose();
        },
      },
    );
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <DialogHeader>
        <DialogTitle>Rename conversation</DialogTitle>
        <DialogDescription>Give it a name that&apos;s easy to find later.</DialogDescription>
      </DialogHeader>
      {rename.isError && (
        <Alert variant="destructive">
          <AlertDescription>{errorMessage(rename.error)}</AlertDescription>
        </Alert>
      )}
      <div className="space-y-2">
        <Label htmlFor="conversation-title">Name</Label>
        <Input
          id="conversation-title"
          value={title}
          maxLength={MAX_TITLE}
          autoFocus
          onFocus={(e) => e.currentTarget.select()}
          onChange={(e) => setTitle(e.target.value)}
        />
      </div>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose} disabled={rename.isPending}>
          Cancel
        </Button>
        <Button type="submit" disabled={!trimmed || rename.isPending}>
          {rename.isPending && <Loader2 className="animate-spin" />} Save
        </Button>
      </DialogFooter>
    </form>
  );
}

/** Confirm and delete a conversation. ``onDeleted`` runs after a successful delete. */
export function DeleteConversationDialog({
  conversation,
  onClose,
  onDeleted,
}: {
  conversation: Target | null;
  onClose: () => void;
  onDeleted?: (id: string) => void;
}) {
  const remove = useDeleteConversation();

  function confirmDelete() {
    if (!conversation) return;
    const { id } = conversation;
    remove.mutate(id, {
      onSuccess: () => {
        toast.success("Conversation deleted");
        onDeleted?.(id);
        onClose();
      },
    });
  }

  return (
    <Dialog
      open={conversation !== null}
      onOpenChange={(open) => {
        if (!open && !remove.isPending) {
          remove.reset();
          onClose();
        }
      }}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Delete conversation?</DialogTitle>
          <DialogDescription>
            <span className="font-medium text-foreground">“{conversation?.title}”</span> and its messages will be
            removed from your conversations. This can&apos;t be undone.
          </DialogDescription>
        </DialogHeader>
        {remove.isError && (
          <Alert variant="destructive">
            <AlertDescription>{errorMessage(remove.error)}</AlertDescription>
          </Alert>
        )}
        <DialogFooter>
          <Button
            variant="outline"
            disabled={remove.isPending}
            onClick={() => {
              remove.reset();
              onClose();
            }}
          >
            Cancel
          </Button>
          <Button variant="destructive" onClick={confirmDelete} disabled={remove.isPending}>
            {remove.isPending && <Loader2 className="animate-spin" />} Delete
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
