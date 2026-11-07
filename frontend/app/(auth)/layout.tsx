import { BrainCircuit, FileSearch, Quote, ShieldCheck } from "lucide-react";

export default function AuthLayout({ children }: LayoutProps<"/">) {
  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <section className="hidden flex-col justify-between bg-sidebar p-10 text-sidebar-foreground lg:flex">
        <div className="flex items-center gap-2.5">
          <span className="grid size-10 place-items-center rounded-xl bg-sidebar-primary text-white shadow">
            <BrainCircuit className="size-5" />
          </span>
          <span className="text-lg font-semibold text-white">DocuMind</span>
        </div>
        <div className="max-w-md space-y-8">
          <h2 className="text-3xl font-semibold leading-tight text-white">
            Ask your documents anything — and see exactly where the answer came from.
          </h2>
          <ul className="space-y-4 text-sm">
            {[
              [FileSearch, "Hybrid semantic + keyword search over PDF, Word and Excel files"],
              [Quote, "Every answer cites the page, section or spreadsheet rows it is based on"],
              [ShieldCheck, "Answers only use documents you are allowed to read"],
            ].map(([Icon, text]) => {
              const I = Icon as typeof FileSearch;
              return (
                <li key={text as string} className="flex items-start gap-3">
                  <I className="mt-0.5 size-4 shrink-0 text-sidebar-primary" />
                  <span>{text as string}</span>
                </li>
              );
            })}
          </ul>
        </div>
        <p className="text-xs text-sidebar-foreground/60">Enterprise Document Intelligence Platform</p>
      </section>
      <section className="flex items-center justify-center p-6">
        <div className="w-full max-w-sm">{children}</div>
      </section>
    </div>
  );
}
