"use client";

import type { ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Clock, FileSpreadsheet, FileText, Loader2, RefreshCw, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { DocumentRead, DocumentStatus, FileType } from "@/types";

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 border-b bg-card px-4 py-5 sm:flex-row sm:items-center sm:justify-between sm:px-8">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function PageBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("mx-auto w-full max-w-7xl space-y-6 p-4 sm:p-8", className)}>{children}</div>;
}

const STATUS: Record<DocumentStatus, { label: string; className: string; icon: typeof Clock; spin?: boolean }> = {
  uploaded: { label: "Uploaded", className: "bg-secondary text-secondary-foreground", icon: Clock },
  queued: { label: "Queued", className: "bg-secondary text-secondary-foreground", icon: Clock },
  processing: { label: "Processing", className: "bg-accent text-accent-foreground", icon: Loader2, spin: true },
  retrying: { label: "Retrying", className: "bg-warning/15 text-warning", icon: RefreshCw, spin: true },
  indexed: { label: "Completed", className: "bg-success/12 text-success", icon: CheckCircle2 },
  failed: { label: "Failed", className: "bg-destructive/10 text-destructive", icon: XCircle },
};

export function StatusBadge({ status }: { status: DocumentStatus }) {
  const s = STATUS[status];
  const Icon = s.icon;
  return (
    <Badge variant="secondary" className={cn("gap-1 border-transparent font-medium", s.className)}>
      <Icon className={cn("size-3", s.spin && "animate-spin")} aria-hidden />
      {s.label}
    </Badge>
  );
}

/** Statuses in which processing is still going on (the UI keeps refreshing while any document is in one). */
export const IN_FLIGHT: ReadonlySet<DocumentStatus> = new Set(["uploaded", "queued", "processing", "retrying"]);

const STAGE: Record<string, string> = {
  parse: "Reading the file",
  chunk: "Splitting into sections",
  embed: "Creating embeddings",
  store: "Saving to the index",
  finalize: "Finishing up",
};

/** One short line under the status badge: the current step, a pending retry, or why it failed. */
export function ProcessingNote({
  doc,
  className,
}: {
  doc: Pick<DocumentRead, "status" | "processing" | "error_message">;
  className?: string;
}) {
  const p = doc.processing;
  const retry = p && p.retry_count > 0 ? ` · retry ${p.retry_count}` : "";
  let text: string | null = null;
  let tone = "text-muted-foreground";
  if (doc.status === "queued") text = "Waiting for a free worker";
  else if (doc.status === "processing") text = `${(p?.stage && STAGE[p.stage]) || "Working"}${retry}`;
  else if (doc.status === "retrying") text = `Temporary problem, trying again automatically${retry}`;
  else if (doc.status === "failed") {
    text = doc.error_message ?? "Processing failed";
    tone = "text-destructive";
  }
  if (!text) return null;
  return (
    <span className={cn("block max-w-xs truncate text-xs", tone, className)} title={text}>
      {text}
    </span>
  );
}

export function FileIcon({ type, className }: { type: FileType; className?: string }) {
  const sheet = type === "xlsx" || type === "xls";
  const Icon = sheet ? FileSpreadsheet : FileText;
  const color = type === "pdf" ? "text-red-500" : sheet ? "text-emerald-600" : "text-blue-600";
  return <Icon className={cn("size-5 shrink-0", color, className)} aria-hidden />;
}

export function EmptyState({
  icon: Icon = FileText,
  title,
  description,
  action,
}: {
  icon?: typeof FileText;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed bg-card px-6 py-14 text-center">
      <span className="grid size-12 place-items-center rounded-full bg-accent text-accent-foreground">
        <Icon className="size-6" />
      </span>
      <div>
        <p className="font-medium">{title}</p>
        {description && <p className="mt-1 max-w-md text-sm text-muted-foreground">{description}</p>}
      </div>
      {action}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
      <AlertTriangle className="size-4 shrink-0" />
      <span className="flex-1">{message}</span>
      {onRetry && (
        <button className="font-medium underline-offset-4 hover:underline" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}
