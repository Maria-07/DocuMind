import { ConversationList } from "@/features/chat/conversation-list";

export default function ChatLayout({ children }: LayoutProps<"/chat">) {
  return (
    <div className="flex h-[calc(100dvh-3.5rem)] lg:h-dvh">
      <aside className="hidden w-72 shrink-0 border-r bg-card md:block">
        <ConversationList />
      </aside>
      <section className="flex min-w-0 flex-1 flex-col">{children}</section>
    </div>
  );
}
