"use client";

import { useMemo, useState } from "react";
import { Loader2, Share2, Trash2, Users, User as UserIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  type GrantInput,
  type PermissionsRead,
  useGroups,
  useMembers,
  usePermissions,
  useSetPermissions,
} from "@/features/insights/queries";
import { errorMessage } from "@/lib/api";
import type { DocumentVisibility } from "@/types";

type Grant = Pick<GrantInput, "principal_type" | "principal_id" | "permission">;

const VISIBILITY_NOTE: Record<DocumentVisibility, string> = {
  private: "This document is private: grants are kept but have no effect until visibility changes.",
  organization: "Everyone in the organization can view it; grants here only add “manage” rights.",
  restricted: "Only the people and groups listed below (plus the owner and admins) can view it.",
};

export function ShareDialog({ documentId, visibility }: { documentId: string; visibility: DocumentVisibility }) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline">
          <Share2 /> Share
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Share document</DialogTitle>
          <DialogDescription>{VISIBILITY_NOTE[visibility]}</DialogDescription>
        </DialogHeader>
        {open && <ShareBody documentId={documentId} onDone={() => setOpen(false)} />}
      </DialogContent>
    </Dialog>
  );
}

function ShareBody({ documentId, onDone }: { documentId: string; onDone: () => void }) {
  const permissions = usePermissions(documentId, true);
  if (permissions.isLoading) return <Skeleton className="h-40" />;
  if (!permissions.data) return <p className="text-sm text-destructive">{errorMessage(permissions.error)}</p>;
  return <ShareForm initial={permissions.data} documentId={documentId} onDone={onDone} />;
}

function ShareForm({ initial, documentId, onDone }: { initial: PermissionsRead; documentId: string; onDone: () => void }) {
  const members = useMembers();
  const groups = useGroups();
  const save = useSetPermissions(documentId);
  const [grants, setGrants] = useState<Grant[]>(
    initial.grants.map(({ principal_type, principal_id, permission }) => ({ principal_type, principal_id, permission })),
  );
  const [pick, setPick] = useState("");

  const names = useMemo(() => {
    const m = new Map<string, string>();
    members.data?.forEach((u) => m.set(`user:${u.id}`, `${u.full_name} · ${u.email}`));
    groups.data?.forEach((g) => m.set(`group:${g.id}`, g.name));
    return m;
  }, [members.data, groups.data]);

  const taken = new Set(grants.map((g) => `${g.principal_type}:${g.principal_id}`));
  const candidates = [
    ...(groups.data ?? []).map((g) => ({ key: `group:${g.id}`, label: `${g.name} (group)` })),
    ...(members.data ?? [])
      .filter((u) => u.id !== initial.owner_id)
      .map((u) => ({ key: `user:${u.id}`, label: `${u.full_name} · ${u.email}` })),
  ].filter((c) => !taken.has(c.key));

  function add(key: string) {
    const [type, id] = key.split(":");
    setGrants((g) => [...g, { principal_type: type as Grant["principal_type"], principal_id: id, permission: "view" }]);
    setPick("");
  }

  return (
    <div className="space-y-4">
      <Select value={pick} onValueChange={add} disabled={!candidates.length}>
        <SelectTrigger className="w-full" aria-label="Add person or group">
          <SelectValue placeholder={candidates.length ? "Add a person or group…" : "Everyone is already listed"} />
        </SelectTrigger>
        <SelectContent>
          {candidates.map((c) => (
            <SelectItem key={c.key} value={c.key}>
              {c.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <ul className="max-h-72 divide-y overflow-y-auto rounded-lg border">
        {grants.length === 0 && <li className="p-4 text-sm text-muted-foreground">Not shared with anyone yet.</li>}
        {grants.map((g, i) => {
          const key = `${g.principal_type}:${g.principal_id}`;
          const Icon = g.principal_type === "group" ? Users : UserIcon;
          return (
            <li key={key} className="flex items-center gap-3 p-3 text-sm">
              <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
              <span className="min-w-0 flex-1 truncate">{names.get(key) ?? "Unknown or deactivated"}</span>
              <Select
                value={g.permission}
                onValueChange={(v) =>
                  setGrants((all) => all.map((x, j) => (j === i ? { ...x, permission: v as Grant["permission"] } : x)))
                }
              >
                <SelectTrigger className="w-28" aria-label="Permission">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="view">Can view</SelectItem>
                  <SelectItem value="manage">Can manage</SelectItem>
                </SelectContent>
              </Select>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Remove"
                onClick={() => setGrants((all) => all.filter((_, j) => j !== i))}
              >
                <Trash2 />
              </Button>
            </li>
          );
        })}
      </ul>

      <DialogFooter>
        <Button variant="outline" onClick={onDone}>
          Cancel
        </Button>
        <Button
          disabled={save.isPending}
          onClick={() =>
            save.mutate(grants, {
              onSuccess: () => {
                toast.success("Sharing updated");
                onDone();
              },
              onError: (e) => toast.error(errorMessage(e)),
            })
          }
        >
          {save.isPending && <Loader2 className="animate-spin" />} Save
        </Button>
      </DialogFooter>
    </div>
  );
}
