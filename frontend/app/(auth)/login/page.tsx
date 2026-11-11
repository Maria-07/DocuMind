"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useRegistrationOpen } from "@/lib/auth-config";

function safeNext(value: string | null) {
  // Only allow in-app relative paths (no open redirects).
  return value && value.startsWith("/") && !value.startsWith("//") ? value : "/";
}

// Optional demo account, set per deployment (Vercel env vars, never in the repository). When both are set the
// form is pre-filled and says so. Anyone who opens the page can read them, so use a low-privilege account.
const DEMO_EMAIL = process.env.NEXT_PUBLIC_DEMO_EMAIL ?? "";
const DEMO_PASSWORD = process.env.NEXT_PUBLIC_DEMO_PASSWORD ?? "";
const DEMO = Boolean(DEMO_EMAIL && DEMO_PASSWORD);

function LoginForm() {
  const { login, status } = useAuth();
  const registrationOpen = useRegistrationOpen();
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  const [email, setEmail] = useState(DEMO ? DEMO_EMAIL : "");
  const [password, setPassword] = useState(DEMO ? DEMO_PASSWORD : "");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (status === "authenticated") router.replace(next);
  }, [status, router, next]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      await login(email, password);
      router.replace(next);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
        <p className="text-sm text-muted-foreground">Welcome back. Sign in to your workspace.</p>
      </div>
      {DEMO && (
        <Alert>
          <AlertDescription>Demo account: the sign-in details are filled in. Just press Sign in.</AlertDescription>
        </Alert>
      )}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <div className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="email">Work e-mail</Label>
          <Input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
      </div>
      <Button type="submit" className="h-10 w-full" disabled={pending}>
        {pending && <Loader2 className="animate-spin" />} Sign in
      </Button>
      {registrationOpen && (
        <p className="text-center text-sm text-muted-foreground">
          New organization?{" "}
          <Link href="/register" className="font-medium text-primary hover:underline">
            Create a workspace
          </Link>
        </p>
      )}
    </form>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
