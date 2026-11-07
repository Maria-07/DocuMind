"use client";

import { useState } from "react";
import { Plus, Trash2, Users } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { type GroupRead, useGroupMutations, useGroups, useMembers } from "@/features/insights/queries";
import { errorMessage } from "@/lib/api";

export function GroupsCard() {
  const { data: groups } = useGroups();
  const { create, remove } = useGroupMutations();
  const [name, setName] = useState("");
  const [editing, setEditing] = useState<GroupRead | null>(null);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Groups</CardTitle>
        <CardDescription>Teams you can share restricted documents with (e.g. Finance, Legal).</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {groups?.length === 0 && <p className="text-sm text-muted-foreground">No groups yet.</p>}
        <ul className="divide-y">
          {groups?.map((g) => (
            <li key={g.id} className="flex items-center gap-3 py-2 text-sm">
              <Users className="size-4 text-muted-foreground" aria-hidden />
              <span className="flex-1 font-medium">{g.name}</span>
              <Badge variant="secondary">
                {g.member_ids.length} member{g.member_ids.length === 1 ? "" : "s"}
              </Badge>
              <Button variant="outline" size="sm" onClick={() => setEditing(g)}>
                Members
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Delete ${g.name}`}
                onClick={() =>
                  confirm(`Delete group "${g.name}"? Documents shared with it lose that access.`) &&
                  remove.mutate(g.id, { onError: (e) => toast.error(errorMessage(e)) })
                }
              >
                <Trash2 />
              </Button>
            </li>
          ))}
        </ul>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate(
              { name: name.trim() },
              { onSuccess: () => setName(""), onError: (err) => toast.error(errorMessage(err)) },
            );
          }}
        >
          <Input placeholder="New group" value={name} onChange={(e) => setName(e.target.value)} maxLength={100} aria-label="New group name" />
          <Button type="submit" variant="outline" disabled={!name.trim() || create.isPending}>
            <Plus /> Add
          </Button>
        </form>
      </CardContent>
      <Dialog open={!!editing} onOpenChange={(o) => !o && setEditing(null)}>
        <DialogContent>
          {editing && <MembersEditor key={editing.id} group={editing} onDone={() => setEditing(null)} />}
        </DialogContent>
      </Dialog>
    </Card>
  );
}

function MembersEditor({ group, onDone }: { group: GroupRead; onDone: () => void }) {
  const { data: members } = useMembers();
  const { setMembers } = useGroupMutations();
  const [selected, setSelected] = useState(new Set(group.member_ids));
  const [filter, setFilter] = useState("");
  const shown = (members ?? []).filter((m) =>
    `${m.full_name} ${m.email}`.toLowerCase().includes(filter.trim().toLowerCase()),
  );
  return (
    <>
      <DialogHeader>
        <DialogTitle>{group.name} members</DialogTitle>
        <DialogDescription>{selected.size} selected</DialogDescription>
      </DialogHeader>
      <Input placeholder="Filter people…" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter people" />
      <ul className="max-h-72 space-y-1 overflow-y-auto">
        {shown.map((m) => (
          <li key={m.id}>
            <label className="flex cursor-pointer items-center gap-3 rounded-md px-2 py-1.5 text-sm hover:bg-muted">
              <Checkbox
                checked={selected.has(m.id)}
                onCheckedChange={(v) =>
                  setSelected((s) => {
                    const next = new Set(s);
                    if (v) next.add(m.id);
                    else next.delete(m.id);
                    return next;
                  })
                }
              />
              <span className="font-medium">{m.full_name}</span>
              <span className="truncate text-muted-foreground">{m.email}</span>
            </label>
          </li>
        ))}
      </ul>
      <DialogFooter>
        <Button variant="outline" onClick={onDone}>
          Cancel
        </Button>
        <Button
          disabled={setMembers.isPending}
          onClick={() =>
            setMembers.mutate(
              { id: group.id, user_ids: [...selected] },
              {
                onSuccess: () => {
                  toast.success("Members updated");
                  onDone();
                },
                onError: (e) => toast.error(errorMessage(e)),
              },
            )
          }
        >
          Save
        </Button>
      </DialogFooter>
    </>
  );
}
