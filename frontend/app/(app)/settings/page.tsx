"use client";

import { useState } from "react";
import { Loader2, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { PageBody, PageHeader } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { useCategoryMutations, useChangePassword, useOrganization, useUpdateOrganization } from "@/features/admin/queries";
import { useCategories } from "@/features/documents/queries";
import { GroupsCard } from "@/features/insights/groups-card";
import { errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { DocumentVisibility, OrganizationRead } from "@/types";

function OrganizationCard() {
  const { data: org } = useOrganization();
  // Mount the form only once loaded, keyed by name, so its local state starts from server data.
  return org ? <OrganizationForm key={org.name} org={org} /> : null;
}

function OrganizationForm({ org }: { org: OrganizationRead }) {
  const update = useUpdateOrganization();
  const [name, setName] = useState(org.name);
  const save = (body: Parameters<typeof update.mutate>[0], ok = "Saved") =>
    update.mutate(body, { onSuccess: () => toast.success(ok), onError: (e) => toast.error(errorMessage(e)) });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Organization</CardTitle>
        <CardDescription>Workspace name and document defaults.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim() && name !== org.name) save({ name: name.trim() }, "Organization renamed");
          }}
        >
          <div className="flex-1 space-y-2">
            <Label htmlFor="org-name">Name</Label>
            <div className="flex gap-2">
              <Input id="org-name" value={name} onChange={(e) => setName(e.target.value)} />
              <Button type="submit" variant="outline" disabled={!name.trim() || name === org.name}>
                Save
              </Button>
            </div>
          </div>
        </form>
        <Separator />
        <div className="flex items-center justify-between gap-4">
          <div>
            <Label htmlFor="users-upload">Users can upload documents</Label>
            <p className="text-sm text-muted-foreground">By default only managers and admins can upload.</p>
          </div>
          <Switch
            id="users-upload"
            checked={org.settings.users_can_upload ?? false}
            onCheckedChange={(v) => save({ settings: { users_can_upload: v } })}
          />
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <Label>Default visibility for new uploads</Label>
            <p className="text-sm text-muted-foreground">Uploaders can still choose per upload.</p>
          </div>
          <Select
            value={org.settings.default_document_visibility ?? "organization"}
            onValueChange={(v) => save({ settings: { default_document_visibility: v as DocumentVisibility } })}
          >
            <SelectTrigger className="w-56">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="organization">Everyone in the organization</SelectItem>
              <SelectItem value="restricted">Restricted</SelectItem>
              <SelectItem value="private">Private</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </CardContent>
    </Card>
  );
}

function CategoriesCard({ editable }: { editable: boolean }) {
  const { data: categories } = useCategories();
  const { create, remove } = useCategoryMutations();
  const [name, setName] = useState("");
  return (
    <Card>
      <CardHeader>
        <CardTitle>Categories</CardTitle>
        <CardDescription>Group documents (e.g. Finance, Legal) for filtering and scoped chats.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap gap-2">
          {categories?.length === 0 && <p className="text-sm text-muted-foreground">No categories yet.</p>}
          {categories?.map((c) => (
            <Badge key={c.id} variant="secondary" className="gap-1.5 py-1 pr-1 pl-2.5 text-sm">
              {c.name}
              {editable && (
                <button
                  aria-label={`Delete ${c.name}`}
                  className="rounded p-0.5 text-muted-foreground hover:text-destructive"
                  onClick={() =>
                    confirm(`Delete category "${c.name}"? Documents keep existing, without a category.`) &&
                    remove.mutate(c.id, { onError: (e) => toast.error(errorMessage(e)) })
                  }
                >
                  <Trash2 className="size-3.5" />
                </button>
              )}
            </Badge>
          ))}
        </div>
        {editable && (
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
            <Input placeholder="New category" value={name} onChange={(e) => setName(e.target.value)} maxLength={100} aria-label="New category name" />
            <Button type="submit" variant="outline" disabled={!name.trim() || create.isPending}>
              <Plus /> Add
            </Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}

function PasswordCard() {
  const change = useChangePassword();
  const { logout } = useAuth();
  const [form, setForm] = useState({ current_password: "", new_password: "" });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Password</CardTitle>
        <CardDescription>Changing your password signs you out on every device.</CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="grid gap-4 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            change.mutate(form, {
              onSuccess: async () => {
                toast.success("Password changed — please sign in again");
                await logout();
              },
              onError: (err) => toast.error(errorMessage(err)),
            });
          }}
        >
          <div className="space-y-2">
            <Label htmlFor="cur">Current password</Label>
            <Input id="cur" type="password" autoComplete="current-password" required value={form.current_password} onChange={(e) => setForm({ ...form, current_password: e.target.value })} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="new">New password</Label>
            <Input id="new" type="password" autoComplete="new-password" required minLength={10} value={form.new_password} onChange={(e) => setForm({ ...form, new_password: e.target.value })} />
          </div>
          <div className="sm:col-span-2">
            <Button type="submit" disabled={change.isPending}>
              {change.isPending && <Loader2 className="animate-spin" />} Change password
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

export default function SettingsPage() {
  const { can } = useAuth();
  return (
    <>
      <PageHeader title="Settings" description="Workspace configuration and your account." />
      <PageBody className="max-w-4xl">
        {can("organization:manage") && <OrganizationCard />}
        <CategoriesCard editable={can("categories:manage")} />
        {can("groups:manage") && <GroupsCard />}
        <PasswordCard />
      </PageBody>
    </>
  );
}
