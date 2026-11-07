import { CheckCircle2, Clock, Loader2, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const STATUS: Record<string, { label: string; icon: typeof Clock; className: string; spin?: boolean }> = {
  queued: { label: "Queued", icon: Clock, className: "bg-secondary text-secondary-foreground" },
  running: { label: "Running", icon: Loader2, className: "bg-accent text-accent-foreground", spin: true },
  complete: { label: "Complete", icon: CheckCircle2, className: "bg-success/12 text-success" },
  failed: { label: "Failed", icon: XCircle, className: "bg-destructive/10 text-destructive" },
};

export function RunStatus({ status }: { status: string }) {
  const s = STATUS[status] ?? STATUS.queued;
  const Icon = s.icon;
  return (
    <Badge variant="secondary" className={cn("gap-1 border-transparent font-medium", s.className)}>
      <Icon className={cn("size-3", s.spin && "animate-spin")} aria-hidden />
      {s.label}
    </Badge>
  );
}
