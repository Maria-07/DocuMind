"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Renders a Markdown table (how extracted tables are stored) as a real table. No raw HTML. */
export function MarkdownTable({ markdown }: { markdown: string }) {
  return (
    <div className="overflow-x-auto rounded-lg border">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          table: ({ children }) => <table className="w-full text-left text-sm">{children}</table>,
          th: ({ children }) => <th className="border-b bg-muted px-3 py-2 font-medium">{children}</th>,
          td: ({ children }) => <td className="border-b px-3 py-2 align-top">{children}</td>,
          p: ({ children }) => <p className="p-3 text-sm whitespace-pre-wrap">{children}</p>,
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
