"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Loader2 } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useRegistrationOpen } from "@/lib/auth-config";

export default function RegisterPage() {
  const { register } = useAuth();
  const registrationOpen = useRegistrationOpen();
  const router = useRouter();
  const [form, setForm] = useState({ full_name: "", email: "", organization_name: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      await register(form);
      router.replace("/");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPending(false);
    }
  }

  if (registrationOpen === false) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold tracking-tight">Sign-up is closed</h1>
        <p className="text-sm text-muted-foreground">
          New workspaces are created by an administrator. If you already have an account, sign in.
        </p>
        <Button asChild className="h-10 w-full">
          <Link href="/login">Sign in</Link>
        </Button>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} className="space-y-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Create your workspace</h1>
        <p className="text-sm text-muted-foreground">You&apos;ll be the administrator of your organization.</p>
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <div className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="full_name">Full name</Label>
          <Input id="full_name" autoComplete="name" required value={form.full_name} onChange={set("full_name")} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="organization_name">Organization</Label>
          <Input id="organization_name" autoComplete="organization" required minLength={2} value={form.organization_name} onChange={set("organization_name")} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="email">Work e-mail</Label>
          <Input id="email" type="email" autoComplete="email" required value={form.email} onChange={set("email")} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            type="password"
            autoComplete="new-password"
            required
            minLength={10}
            value={form.password}
            onChange={set("password")}
            aria-describedby="password-hint"
          />
          <p id="password-hint" className="text-xs text-muted-foreground">
            At least 10 characters, including a letter and a digit.
          </p>
        </div>
      </div>
      <Button type="submit" className="h-10 w-full" disabled={pending}>
        {pending && <Loader2 className="animate-spin" />} Create workspace
      </Button>
      <p className="text-center text-sm text-muted-foreground">
        Already have an account?{" "}
        <Link href="/login" className="font-medium text-primary hover:underline">
          Sign in
        </Link>
      </p>
    </form>
  );
}
