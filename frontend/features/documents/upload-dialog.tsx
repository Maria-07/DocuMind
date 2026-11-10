"use client";

import { useCallback, useRef, useState } from "react";
import { CheckCircle2, Clock, CloudUpload, Copy, Loader2, Upload, X, XCircle } from "lucide-react";
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
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ApiError, errorMessage } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { DocumentVisibility, UploadResult } from "@/types";

import { useCategories, useUploadDocument } from "./queries";

const ACCEPT = [".pdf", ".docx", ".xlsx", ".xls"];
const MAX_MB = 50; // mirrors the server default; the server is the authority
const MAX_FILES = 20; // files waiting or uploading at once
// Files uploaded in parallel. Each is its own request, so one large file occupies one slot and
// the rest keep moving through the others.
const CONCURRENCY = 3;

const VISIBILITY: { value: DocumentVisibility; label: string; hint: string }[] = [
  { value: "organization", label: "Everyone in the organization", hint: "All members can read and ask about it" },
  { value: "restricted", label: "Restricted", hint: "Only you, admins and people you share with" },
  { value: "private", label: "Private", hint: "Only you and admins" },
];

type Phase = "ready" | "queued" | "uploading" | "done";

interface Item {
  id: string;
  file: File;
  problem: string | null;
  phase: Phase;
  progress: number;
  result?: UploadResult;
}

interface Job {
  file: File;
  visibility: DocumentVisibility;
  categoryId?: string;
}

function clientCheck(file: File): string | null {
  const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
  if (!ACCEPT.includes(ext)) return "Unsupported type (PDF, DOCX, XLSX, XLS)";
  if (file.size > MAX_MB * 1024 * 1024) return `Larger than ${MAX_MB} MB`;
  if (file.size === 0) return "Empty file";
  return null;
}

function failedResult(filename: string, err: unknown): UploadResult {
  const code = err instanceof ApiError ? err.code : "upload_failed";
  return { filename, status: "rejected", error: { code, message: errorMessage(err) } };
}

const isBusy = (i: Item) => i.phase === "queued" || i.phase === "uploading";

export function UploadDialog() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Item[]>([]);
  const [visibility, setVisibility] = useState<DocumentVisibility>("organization");
  const [categoryId, setCategoryId] = useState<string>("none");
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const uploadFile = useUploadDocument();
  const { data: categories } = useCategories();

  // The queue lives in refs, so uploads keep going if the dialog is closed mid-batch.
  const jobs = useRef(new Map<string, Job>());
  const waiting = useRef<string[]>([]);
  const active = useRef(0);
  const batch = useRef({ accepted: 0, duplicate: 0, rejected: 0 });

  const update = useCallback((id: string, patch: Partial<Item>) => {
    setItems((prev) => prev.map((i) => (i.id === id ? { ...i, ...patch } : i)));
  }, []);

  const finishBatchIfIdle = useCallback(() => {
    if (active.current > 0 || waiting.current.length > 0) return;
    const { accepted, duplicate, rejected } = batch.current;
    batch.current = { accepted: 0, duplicate: 0, rejected: 0 };
    if (accepted) toast.success(`${accepted} document${accepted > 1 ? "s" : ""} uploaded — indexing started`);
    if (rejected || duplicate) toast.warning(`${rejected} rejected, ${duplicate} duplicate`);
  }, []);

  // Starts up to CONCURRENCY upload slots. Each slot takes the next waiting file until none are
  // left, so files added mid-batch are picked up by the slots already running.
  const pump = useCallback(() => {
    const runSlot = async () => {
      for (let id = waiting.current.shift(); id !== undefined; id = waiting.current.shift()) {
        const job = jobs.current.get(id);
        if (!job) continue;
        const itemId = id;
        update(itemId, { phase: "uploading", progress: 0 });
        let result: UploadResult;
        try {
          const res = await uploadFile({
            file: job.file,
            visibility: job.visibility,
            categoryId: job.categoryId,
            onProgress: (progress) => update(itemId, { progress }),
          });
          result = res.results[0] ?? failedResult(job.file.name, new Error("No result returned"));
        } catch (err) {
          result = failedResult(job.file.name, err);
        }
        batch.current[result.status] += 1;
        jobs.current.delete(itemId);
        update(itemId, { phase: "done", progress: 1, result });
      }
      active.current -= 1;
      finishBatchIfIdle();
    };
    // runSlot takes its first file synchronously (before its first await), so this loop sees the
    // queue shrink and never starts more slots than there are files.
    while (active.current < CONCURRENCY && waiting.current.length > 0) {
      active.current += 1;
      void runSlot();
    }
  }, [update, uploadFile, finishBatchIfIdle]);

  const addFiles = useCallback((list: FileList | File[]) => {
    setItems((prev) => {
      const seen = new Set(prev.filter((i) => i.phase !== "done").map((i) => `${i.file.name}:${i.file.size}`));
      const room = MAX_FILES - prev.filter((i) => i.phase !== "done").length;
      const fresh = Array.from(list)
        .filter((f) => !seen.has(`${f.name}:${f.size}`))
        .slice(0, Math.max(room, 0))
        .map<Item>((file) => ({
          id: crypto.randomUUID(),
          file,
          problem: clientCheck(file),
          phase: "ready",
          progress: 0,
        }));
      return [...prev, ...fresh];
    });
  }, []);

  const ready = items.filter((i) => i.phase === "ready" && !i.problem);
  const busy = items.some(isBusy);

  function submit() {
    const ids = new Set(ready.map((i) => i.id));
    for (const item of ready) {
      jobs.current.set(item.id, {
        file: item.file,
        visibility,
        categoryId: categoryId === "none" ? undefined : categoryId,
      });
      waiting.current.push(item.id);
    }
    // Drop files that failed the local check; they were never going to be sent.
    setItems((prev) =>
      prev
        .filter((i) => !(i.phase === "ready" && i.problem))
        .map((i) => (ids.has(i.id) ? { ...i, phase: "queued" } : i)),
    );
    pump();
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        // Uploads in progress continue in the background; finished and unsent rows are cleared.
        if (!o) setItems((prev) => prev.filter(isBusy));
      }}
    >
      <DialogTrigger asChild>
        <Button>
          <Upload /> Upload documents
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Upload documents</DialogTitle>
          <DialogDescription>
            PDF, Word (.docx) and Excel (.xlsx, .xls) — up to {MAX_FILES} files, {MAX_MB} MB each. Files upload in
            parallel; you can close this dialog and they keep going.
          </DialogDescription>
        </DialogHeader>

        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            addFiles(e.dataTransfer.files);
          }}
          className={cn(
            "flex w-full flex-col items-center gap-2 rounded-xl border-2 border-dashed p-8 text-center transition-colors",
            dragging ? "border-primary bg-accent" : "hover:border-primary/50 hover:bg-muted/50",
          )}
        >
          <CloudUpload className="size-8 text-primary" />
          <span className="text-sm font-medium">Drag & drop files here, or click to browse</span>
          <span className="text-xs text-muted-foreground">Files are validated by content, not just extension</span>
        </button>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPT.join(",")}
          className="hidden"
          onChange={(e) => {
            if (e.target.files) addFiles(e.target.files);
            e.target.value = "";
          }}
        />

        {items.length > 0 && (
          <ul className="max-h-64 space-y-1 overflow-y-auto" aria-live="polite">
            {items.map((item) => (
              <FileRow
                key={item.id}
                item={item}
                onRemove={() => setItems((prev) => prev.filter((i) => i.id !== item.id))}
              />
            ))}
          </ul>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label>Who can see these documents</Label>
            <Select value={visibility} onValueChange={(v) => setVisibility(v as DocumentVisibility)}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {VISIBILITY.map((v) => (
                  <SelectItem key={v.value} value={v.value}>
                    {v.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">{VISIBILITY.find((v) => v.value === visibility)?.hint}</p>
          </div>
          <div className="space-y-2">
            <Label>Category</Label>
            <Select value={categoryId} onValueChange={setCategoryId}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">No category</SelectItem>
                {categories?.map((c) => (
                  <SelectItem key={c.id} value={c.id}>
                    {c.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            {busy ? "Close" : items.some((i) => i.phase === "done") ? "Done" : "Cancel"}
          </Button>
          <Button onClick={submit} disabled={ready.length === 0}>
            {busy && ready.length === 0 && <Loader2 className="animate-spin" />}
            {ready.length > 0
              ? `Upload ${ready.length} file${ready.length > 1 ? "s" : ""}`
              : busy
                ? "Uploading…"
                : "Upload"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function FileRow({ item, onRemove }: { item: Item; onRemove: () => void }) {
  const { file, phase, problem, progress, result } = item;
  return (
    <li className="rounded-lg bg-muted/60 px-3 py-1.5 text-sm">
      <div className="flex items-center gap-2">
        {phase === "queued" && <Clock className="size-4 shrink-0 text-muted-foreground" aria-hidden />}
        {phase === "uploading" && <Loader2 className="size-4 shrink-0 animate-spin text-primary" aria-hidden />}
        {result?.status === "accepted" && <CheckCircle2 className="size-4 shrink-0 text-success" aria-hidden />}
        {result?.status === "duplicate" && <Copy className="size-4 shrink-0 text-warning" aria-hidden />}
        {result?.status === "rejected" && <XCircle className="size-4 shrink-0 text-destructive" aria-hidden />}
        <span className="min-w-0 flex-1 truncate font-medium">{file.name}</span>
        <span className={cn("shrink-0 text-xs", problem ? "text-destructive" : "text-muted-foreground")}>
          {problem ??
            (phase === "queued"
              ? "Waiting…"
              : phase === "uploading"
                ? `${Math.round(progress * 100)}% of ${formatBytes(file.size)}`
                : result?.status === "accepted"
                  ? "Uploaded"
                  : result?.status === "duplicate"
                    ? "Duplicate"
                    : result?.status === "rejected"
                      ? "Rejected"
                      : formatBytes(file.size))}
        </span>
        {(phase === "ready" || phase === "done") && (
          <button
            type="button"
            aria-label={`Remove ${file.name}`}
            onClick={onRemove}
            className="text-muted-foreground hover:text-foreground"
          >
            <X className="size-4" />
          </button>
        )}
      </div>
      {phase === "uploading" && (
        <Progress value={Math.round(progress * 100)} className="mt-1.5 h-1" aria-label={`Uploading ${file.name}`} />
      )}
      {result?.error && <p className="mt-0.5 text-xs text-muted-foreground">{result.error.message}</p>}
    </li>
  );
}
