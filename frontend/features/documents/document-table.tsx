"use client";

import Link from "next/link";
import { Download, MoreHorizontal, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { FileIcon, StatusBadge } from "@/components/common";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { downloadFile, errorMessage } from "@/lib/api";
import { formatBytes, timeAgo } from "@/lib/format";
import type { CategoryRead, DocumentRead } from "@/types";

import { useDeleteDocument, useReprocessDocument } from "./queries";

const VISIBILITY_LABEL = { organization: "Organization", restricted: "Restricted", private: "Private" } as const;

export function DocumentTable({
  documents,
  categories,
  loading,
}: {
  documents: DocumentRead[];
  categories?: CategoryRead[];
  loading?: boolean;
}) {
  const remove = useDeleteDocument();
  const reprocess = useReprocessDocument();
  const categoryName = (id: string | null) => categories?.find((c) => c.id === id)?.name;

  if (loading) {
    return (
      <div className="space-y-2 p-4">
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead className="hidden md:table-cell">Category</TableHead>
          <TableHead>Status</TableHead>
          <TableHead className="hidden lg:table-cell">Access</TableHead>
          <TableHead className="hidden sm:table-cell">Size</TableHead>
          <TableHead className="hidden md:table-cell">Uploaded</TableHead>
          <TableHead className="w-10">
            <span className="sr-only">Actions</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {documents.map((d) => (
          <TableRow key={d.id}>
            <TableCell className="max-w-0 w-full">
              <Link href={`/documents/${d.id}`} className="flex items-center gap-3 font-medium hover:text-primary">
                <FileIcon type={d.file_type} />
                <span className="min-w-0">
                  <span className="block truncate">{d.title}</span>
                  <span className="block truncate text-xs font-normal text-muted-foreground">{d.original_filename}</span>
                </span>
              </Link>
            </TableCell>
            <TableCell className="hidden text-muted-foreground md:table-cell">{categoryName(d.category_id) ?? "—"}</TableCell>
            <TableCell>
              <StatusBadge status={d.status} />
            </TableCell>
            <TableCell className="hidden text-muted-foreground lg:table-cell">{VISIBILITY_LABEL[d.visibility]}</TableCell>
            <TableCell className="hidden whitespace-nowrap text-muted-foreground sm:table-cell">{formatBytes(d.size_bytes)}</TableCell>
            <TableCell className="hidden whitespace-nowrap text-muted-foreground md:table-cell">{timeAgo(d.created_at)}</TableCell>
            <TableCell>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${d.title}`}>
                    <MoreHorizontal />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem
                    onSelect={() =>
                      downloadFile(`/api/v1/documents/${d.id}/download`, d.original_filename).catch((e) =>
                        toast.error(errorMessage(e)),
                      )
                    }
                  >
                    <Download /> Download
                  </DropdownMenuItem>
                  {d.can_manage && (
                    <>
                      <DropdownMenuItem
                        disabled={d.status === "processing" || d.status === "uploaded"}
                        onSelect={() =>
                          reprocess.mutate(d.id, {
                            onSuccess: () => toast.success("Re-indexing started"),
                            onError: (e) => toast.error(errorMessage(e)),
                          })
                        }
                      >
                        <RefreshCw /> Reprocess
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        variant="destructive"
                        onSelect={() => {
                          if (!confirm(`Delete "${d.title}"? It will no longer be searchable.`)) return;
                          remove.mutate(d.id, {
                            onSuccess: () => toast.success("Document deleted"),
                            onError: (e) => toast.error(errorMessage(e)),
                          });
                        }}
                      >
                        <Trash2 /> Delete
                      </DropdownMenuItem>
                    </>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
