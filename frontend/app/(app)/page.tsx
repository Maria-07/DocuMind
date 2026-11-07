"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { AlertTriangle, ArrowRight, CheckCircle2, FileText, Loader2, MessageSquare } from "lucide-react";
import { toast } from "sonner";

import { EmptyState, FileIcon, PageBody, PageHeader, StatusBadge } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Composer } from "@/features/chat/composer";
import { useConversations, useCreateConversation } from "@/features/chat/queries";
import { useDocumentCount, useDocuments } from "@/features/documents/queries";
import { UploadDialog } from "@/features/documents/upload-dialog";
import { errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { timeAgo } from "@/lib/format";
import { cn } from "@/lib/utils";

function Stat({
  label,
  value,
  icon: Icon,
  tone,
  href,
}: {
  label: string;
  value: number | undefined;
  icon: typeof FileText;
  tone: string;
  href: string;
}) {
  return (
    <Link href={href} className="group">
      <Card className="gap-0 py-5 transition-shadow group-hover:shadow-md">
        <CardContent className="flex items-center gap-4">
          <span className={cn("grid size-11 place-items-center rounded-xl", tone)}>
            <Icon className="size-5" />
          </span>
          <div>
            <p className="text-sm text-muted-foreground">{label}</p>
            {value === undefined ? <Skeleton className="mt-1 h-7 w-12" /> : <p className="text-2xl font-semibold tabular-nums">{value}</p>}
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

export default function HomePage() {
  const { me, can } = useAuth();
  const router = useRouter();
  const total = useDocumentCount();
  const indexed = useDocumentCount("indexed");
  const processing = useDocumentCount("processing");
  const failed = useDocumentCount("failed");
  const recent = useDocuments({ page_size: 6 });
  const conversations = useConversations(5);
  const create = useCreateConversation();

  async function ask(question: string) {
    try {
      const conv = await create.mutateAsync({});
      router.push(`/chat/${conv.id}?q=${encodeURIComponent(question)}`);
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  const firstName = me?.user.full_name.split(" ")[0] ?? "";
  return (
    <>
      <PageHeader
        title={`Welcome back${firstName ? `, ${firstName}` : ""}`}
        description={me ? `${me.organization.name} workspace` : undefined}
        actions={can("documents:upload") ? <UploadDialog /> : undefined}
      />
      <PageBody>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Stat label="Total documents" value={total.data} icon={FileText} tone="bg-accent text-accent-foreground" href="/documents" />
          <Stat label="Indexed" value={indexed.data} icon={CheckCircle2} tone="bg-success/12 text-success" href="/documents" />
          <Stat label="Processing" value={processing.data} icon={Loader2} tone="bg-secondary text-secondary-foreground" href="/documents" />
          <Stat label="Failed" value={failed.data} icon={AlertTriangle} tone="bg-destructive/10 text-destructive" href="/documents" />
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Ask your documents</CardTitle>
            <CardDescription>Get answers grounded in your organization&apos;s documents, with citations.</CardDescription>
          </CardHeader>
          <CardContent>
            <Composer onSend={ask} disabled={create.isPending || !indexed.data} />
            {indexed.data === 0 && (
              <p className="mt-2 text-xs text-muted-foreground">Upload and index a document to start asking questions.</p>
            )}
          </CardContent>
        </Card>

        <div className="grid gap-6 lg:grid-cols-5">
          <Card className="lg:col-span-3">
            <CardHeader className="flex flex-row items-center justify-between">
              <div>
                <CardTitle>Recent documents</CardTitle>
                <CardDescription>Latest uploads and their processing status</CardDescription>
              </div>
              <Button variant="ghost" size="sm" asChild>
                <Link href="/documents">
                  View all <ArrowRight />
                </Link>
              </Button>
            </CardHeader>
            <CardContent>
              {recent.isLoading ? (
                <div className="space-y-2">
                  {Array.from({ length: 4 }, (_, i) => (
                    <Skeleton key={i} className="h-12" />
                  ))}
                </div>
              ) : recent.data?.items.length ? (
                <ul className="divide-y">
                  {recent.data.items.map((d) => (
                    <li key={d.id}>
                      <Link href={`/documents/${d.id}`} className="flex items-center gap-3 py-2.5 hover:text-primary">
                        <FileIcon type={d.file_type} />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-medium">{d.title}</span>
                          <span className="block text-xs text-muted-foreground">{timeAgo(d.created_at)}</span>
                        </span>
                        <StatusBadge status={d.status} />
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState title="No documents yet" description="Upload PDF, Word or Excel files to get started." />
              )}
            </CardContent>
          </Card>

          <Card className="lg:col-span-2">
            <CardHeader className="flex flex-row items-center justify-between">
              <div>
                <CardTitle>Recent conversations</CardTitle>
                <CardDescription>Pick up where you left off</CardDescription>
              </div>
              <Button variant="ghost" size="sm" asChild>
                <Link href="/chat">
                  Open chat <ArrowRight />
                </Link>
              </Button>
            </CardHeader>
            <CardContent>
              {conversations.isLoading ? (
                <Skeleton className="h-32" />
              ) : conversations.data?.items.length ? (
                <ul className="divide-y">
                  {conversations.data.items.map((c) => (
                    <li key={c.id}>
                      <Link href={`/chat/${c.id}`} className="flex items-center gap-3 py-2.5 hover:text-primary">
                        <MessageSquare className="size-4 shrink-0 text-muted-foreground" />
                        <span className="min-w-0 flex-1 truncate text-sm">{c.title}</span>
                        <span className="text-xs whitespace-nowrap text-muted-foreground">{timeAgo(c.updated_at)}</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="py-6 text-center text-sm text-muted-foreground">No conversations yet.</p>
              )}
            </CardContent>
          </Card>
        </div>
      </PageBody>
    </>
  );
}
