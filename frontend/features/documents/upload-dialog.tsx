"use client";

import { useCallback, useRef, useState } from "react";
import { CheckCircle2, CloudUpload, Copy, Loader2, Upload, X, XCircle } from "lucide-react";
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
import { errorMessage } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { DocumentVisibility, UploadResult } from "@/types";

import { useCategories, useUploadDocuments } from "./queries";

const ACCEPT = [".pdf", ".docx", ".xlsx", ".xls"];
const MAX_MB = 50; // mirrors the server default; the server is the authority
const MAX_FILES = 20;

const VISIBILITY: { value: DocumentVisibility; label: string; hint: string }[] = [
  { value: "organization", label: "Everyone in the organization", hint: "All members can read and ask about it" },
  { value: "restricted", label: "Restricted", hint: "Only you, admins and people you share with" },
  { value: "private", label: "Private", hint: "Only you and admins" },
];

function clientCheck(file: File): string | null {
  const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
  if (!ACCEPT.includes(ext)) return "Unsupported type (PDF, DOCX, XLSX, XLS)";
  if (file.size > MAX_MB * 1024 * 1024) return `Larger than ${MAX_MB} MB`;
  if (file.size === 0) return "Empty file";
  return null;
}

export function UploadDialog() {
  const [open, setOpen] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [visibility, setVisibility] = useState<DocumentVisibility>("organization");
  const [categoryId, setCategoryId] = useState<string>("none");
  const [progress, setProgress] = useState(0);
  const [results, setResults] = useState<UploadResult[] | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const upload = useUploadDocuments();
  const { data: categories } = useCategories();

  const addFiles = useCallback((list: FileList | File[]) => {
    setResults(null);
    setFiles((prev) => {
      const seen = new Set(prev.map((f) => `${f.name}:${f.size}`));
      return [...prev, ...Array.from(list).filter((f) => !seen.has(`${f.name}:${f.size}`))].slice(0, MAX_FILES);
    });
  }, []);

  const reset = () => {
    setFiles([]);
    setResults(null);
    setProgress(0);
  };

  const valid = files.filter((f) => !clientCheck(f));

  async function submit() {
    setProgress(0);
    try {
      const res = await upload.mutateAsync({
        files: valid,
        visibility,
        categoryId: categoryId === "none" ? undefined : categoryId,
        onProgress: setProgress,
      });
      setResults(res.results);
      setFiles([]);
      if (res.accepted) toast.success(`${res.accepted} document${res.accepted > 1 ? "s" : ""} uploaded — indexing started`);
      if (res.rejected || res.duplicates) toast.warning(`${res.rejected} rejected, ${res.duplicates} duplicate`);
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) reset();
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
          <DialogDescription>PDF, Word (.docx) and Excel (.xlsx, .xls) — up to {MAX_FILES} files, {MAX_MB} MB each.</DialogDescription>
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

        {files.length > 0 && (
          <ul className="max-h-48 space-y-1 overflow-y-auto">
            {files.map((f) => {
              const problem = clientCheck(f);
              return (
                <li key={`${f.name}:${f.size}`} className="flex items-center gap-2 rounded-lg bg-muted/60 px-3 py-1.5 text-sm">
                  <span className="min-w-0 flex-1 truncate">{f.name}</span>
                  <span className={cn("text-xs", problem ? "text-destructive" : "text-muted-foreground")}>
                    {problem ?? formatBytes(f.size)}
                  </span>
                  <button
                    type="button"
                    aria-label={`Remove ${f.name}`}
                    onClick={() => setFiles((prev) => prev.filter((x) => x !== f))}
                    className="text-muted-foreground hover:text-foreground"
                  >
                    <X className="size-4" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        {results && (
          <ul className="max-h-48 space-y-1 overflow-y-auto" aria-live="polite">
            {results.map((r) => (
              <li key={r.filename} className="flex items-start gap-2 text-sm">
                {r.status === "accepted" && <CheckCircle2 className="mt-0.5 size-4 text-success" />}
                {r.status === "duplicate" && <Copy className="mt-0.5 size-4 text-warning" />}
                {r.status === "rejected" && <XCircle className="mt-0.5 size-4 text-destructive" />}
                <span className="min-w-0 flex-1">
                  <span className="font-medium">{r.filename}</span>
                  {r.error && <span className="block text-xs text-muted-foreground">{r.error.message}</span>}
                </span>
              </li>
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

        {upload.isPending && <Progress value={Math.round(progress * 100)} aria-label="Upload progress" />}

        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            {results ? "Done" : "Cancel"}
          </Button>
          <Button onClick={submit} disabled={valid.length === 0 || upload.isPending}>
            {upload.isPending && <Loader2 className="animate-spin" />}
            Upload {valid.length > 0 ? `${valid.length} file${valid.length > 1 ? "s" : ""}` : ""}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
