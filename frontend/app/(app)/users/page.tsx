"use client";

import { useState } from "react";
import { Loader2, Search, ShieldAlert, UserPlus } from "lucide-react";
import { toast } from "sonner";

import { EmptyState, ErrorState, PageBody, PageHeader } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useCreateUser, useUpdateUser, useUsers } from "@/features/admin/queries";
import { errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { timeAgo } from "@/lib/format";
import type { Role } from "@/types";

const ROLES: { value: Role; label: string; hint: string }[] = [
  { value: "admin", label: "Admin", hint: "Full access: users, settings, all documents" },
  { value: "manager", label: "Manager", hint: "Uploads and manages documents" },
  { value: "user", label: "User", hint: "Reads permitted documents and asks questions" },
];

function CreateUserDialog() {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ full_name: "", email: "", role: "user" as Role, password: "" });
  const create = useCreateUser();
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <UserPlus /> Add user
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate(form, {
              onSuccess: () => {
                toast.success(`${form.full_name} was added`);
                setOpen(false);
                setForm({ full_name: "", email: "", role: "user", password: "" });
              },
              onError: (err) => toast.error(errorMessage(err)),
            });
          }}
        >
          <DialogHeader>
            <DialogTitle>Add a user</DialogTitle>
            <DialogDescription>Share the initial password with them securely; they can change it in Settings.</DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="u-name">Full name</Label>
            <Input id="u-name" required value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="u-email">E-mail</Label>
            <Input id="u-email" type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          </div>
          <div className="space-y-2">
            <Label>Role</Label>
            <Select value={form.role} onValueChange={(v) => setForm({ ...form, role: v as Role })}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ROLES.map((r) => (
                  <SelectItem key={r.value} value={r.value}>
                    {r.label} — <span className="text-muted-foreground">{r.hint}</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="u-pass">Initial password</Label>
            <Input
              id="u-pass"
              type="password"
              autoComplete="new-password"
              required
              minLength={10}
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending && <Loader2 className="animate-spin" />} Add user
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function UsersPage() {
  const { can, me } = useAuth();
  const [q, setQ] = useState("");
  const { data, isLoading, isError, error } = useUsers(q);
  const update = useUpdateUser();

  if (!can("users:manage")) {
    return (
      <PageBody>
        <EmptyState icon={ShieldAlert} title="Administrators only" description="You don't have permission to manage users." />
      </PageBody>
    );
  }

  const patch = (id: string, body: { role?: Role; is_active?: boolean }, ok: string) =>
    update.mutate({ id, ...body }, { onSuccess: () => toast.success(ok), onError: (e) => toast.error(errorMessage(e)) });

  return (
    <>
      <PageHeader title="User management" description="Invite people and control what they can do." actions={<CreateUserDialog />} />
      <PageBody>
        <Card className="gap-0 overflow-hidden py-0">
          <div className="border-b p-4">
            <div className="relative max-w-sm">
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input placeholder="Search by name or e-mail…" value={q} onChange={(e) => setQ(e.target.value)} className="pl-9" aria-label="Search users" />
            </div>
          </div>
          {isError ? (
            <div className="p-4">
              <ErrorState message={errorMessage(error)} />
            </div>
          ) : isLoading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 4 }, (_, i) => (
                <Skeleton key={i} className="h-12" />
              ))}
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead className="hidden md:table-cell">Last sign-in</TableHead>
                  <TableHead>Active</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data?.items.map((u) => {
                  const self = u.id === me?.user.id;
                  return (
                    <TableRow key={u.id}>
                      <TableCell>
                        <span className="block font-medium">
                          {u.full_name} {self && <Badge variant="secondary" className="ml-1">You</Badge>}
                        </span>
                        <span className="block text-xs text-muted-foreground">{u.email}</span>
                      </TableCell>
                      <TableCell>
                        <Select value={u.role} onValueChange={(v) => patch(u.id, { role: v as Role }, "Role updated")}>
                          <SelectTrigger className="w-32" size="sm" aria-label={`Role of ${u.full_name}`}>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {ROLES.map((r) => (
                              <SelectItem key={r.value} value={r.value}>
                                {r.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </TableCell>
                      <TableCell className="hidden text-muted-foreground md:table-cell">
                        {u.last_login_at ? timeAgo(u.last_login_at) : "Never"}
                      </TableCell>
                      <TableCell>
                        <Switch
                          checked={u.is_active}
                          aria-label={`${u.is_active ? "Deactivate" : "Activate"} ${u.full_name}`}
                          onCheckedChange={(checked) =>
                            patch(u.id, { is_active: checked }, checked ? "User reactivated" : "User deactivated — signed out everywhere")
                          }
                        />
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </Card>
      </PageBody>
    </>
  );
}
