"use client";

import { AssistantRuntimeProvider, useLocalRuntime, type ChatModelAdapter, type ThreadMessage } from "@assistant-ui/react";
import { Thread } from "@assistant-ui/react-ui";
import { startTransition, useEffect, useMemo, useState } from "react";

import { cancelConversation, createConversation, deleteConversation, listConversations, listMessages } from "@/lib/api";
import { SideNav } from "@/components/layout/side-nav";
import { parseSse } from "@/lib/sse";
import { PROVIDERS, type ChatMessage, type Conversation, type Provider } from "@/lib/types";

function latestUserText(messages: readonly ThreadMessage[]) {
  const user = [...messages].reverse().find((item) => item.role === "user");
  return user?.content.filter((part) => part.type === "text").map((part) => part.text).join("\n").trim() ?? "";
}

function toThreadMessages(messages: ChatMessage[]) {
  return messages.map((item) => ({
    id: item.id,
    role: item.role,
    content: item.content,
    createdAt: item.created_at ? new Date(item.created_at) : undefined,
  }));
}

export function ChatApp() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [provider, setProvider] = useState<Provider>("openai");
  const [model, setModel] = useState(PROVIDERS[0].models[0]);
  const [isCreating, setIsCreating] = useState(false);
  const [newChatError, setNewChatError] = useState<string | null>(null);
  const activeProvider = PROVIDERS.find((item) => item.id === provider) ?? PROVIDERS[0];
  const lastMessageId = messages.at(-1)?.id ?? "empty";
  const runtimeKey = `${activeId ?? "draft"}:${messages.length}:${lastMessageId}`;

  async function refresh(nextActiveId?: string | null) {
    const items = await listConversations();
    startTransition(() => {
      setConversations(items);
      setActiveId((current) => nextActiveId ?? (current && items.some((item) => item.id === current) ? current : items[0]?.id ?? null));
    });
  }

  useEffect(() => {
    void refresh();
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (!activeId) {
      setMessages([]);
      return () => {
        cancelled = true;
      };
    }
    void listMessages(activeId).then((items) => {
      if (!cancelled) startTransition(() => setMessages(items));
    });
    return () => {
      cancelled = true;
    };
  }, [activeId]);

  const adapter = useMemo<ChatModelAdapter>(() => ({
    async *run({ messages: threadMessages, abortSignal }) {
      let cid = activeId;
      if (!cid) {
        const created = await createConversation(latestUserText(threadMessages).slice(0, 48) || "New chat");
        cid = created.id;
        startTransition(() => {
          setActiveId(cid);
          setMessages([]);
        });
        await refresh(created.id);
      }
      const content = latestUserText(threadMessages);
      if (!content || !cid) return;

      const abort = () => void cancelConversation(cid);
      abortSignal.addEventListener("abort", abort, { once: true });
      try {
        const response = await fetch("/api/chat/v1/chat", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ conversation_id: cid, content, provider, model, stream: true }),
          signal: abortSignal,
        });
        let text = "";
        for await (const event of parseSse(response)) {
          if (event.type === "delta") {
            text += event.content;
            yield { content: [{ type: "text", text }] };
          }
          if (event.type === "error") throw new Error(event.message);
        }
      } finally {
        abortSignal.removeEventListener("abort", abort);
        await refresh(cid);
        const latest = await listMessages(cid);
        startTransition(() => setMessages(latest));
      }
    },
  }), [activeId, model, provider]);

  const runtime = useLocalRuntime(adapter, {
    initialMessages: toThreadMessages(messages),
  });

  async function newChat() {
    setIsCreating(true);
    setNewChatError(null);
    try {
      const created = await createConversation("New chat");
      startTransition(() => {
        setActiveId(created.id);
        setMessages([]);
      });
      await refresh(created.id);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to create conversation";
      setNewChatError(message);
    } finally {
      setIsCreating(false);
    }
  }

  async function remove(id: string) {
    await deleteConversation(id);
    await refresh(activeId === id ? null : activeId);
  }

  async function cancelActiveConversation() {
    if (!activeId) return;
    await cancelConversation(activeId);
    await refresh(activeId);
  }

  return (
    <AssistantRuntimeProvider key={runtimeKey} runtime={runtime}>
      <main className="app-shell">
        <aside className="border-r bg-gray-50">
          <SideNav />
          <div className="flex items-center justify-between border-b p-3">
            <div className="text-sm font-semibold">Conversations</div>
            <button className="rounded-md bg-gray-900 px-3 py-1.5 text-sm text-white disabled:opacity-60" disabled={isCreating} onClick={() => void newChat()}>
              {isCreating ? "Creating..." : "New"}
            </button>
          </div>
          {newChatError ? <div className="px-3 pt-2 text-xs text-red-600">{newChatError}</div> : null}
          <div className="space-y-1 p-2">
            {conversations.map((conversation) => (
              <div key={conversation.id} className={`rounded-md border bg-white p-2 ${conversation.id === activeId ? "border-gray-900" : "border-gray-200"}`}>
                <button className="block w-full truncate text-left text-sm" onClick={() => setActiveId(conversation.id)}>
                  {conversation.title || "Untitled"}
                </button>
                <div className="mt-2 flex items-center justify-between text-xs text-gray-500">
                  <span>{conversation.status}</span>
                  <button className="hover:text-red-600" onClick={() => void remove(conversation.id)}>Delete</button>
                </div>
              </div>
            ))}
          </div>
        </aside>

        <section className="flex min-h-0 flex-col">
          <div className="flex flex-wrap items-center gap-3 border-b p-3">
            <select className="rounded-md border px-2 py-1.5 text-sm" value={provider} onChange={(event) => {
              const next = event.target.value as Provider;
              setProvider(next);
              setModel((PROVIDERS.find((item) => item.id === next) ?? PROVIDERS[0]).models[0]);
            }}>
              {PROVIDERS.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
            </select>
            <select className="rounded-md border px-2 py-1.5 text-sm" value={model} onChange={(event) => setModel(event.target.value)}>
              {activeProvider.models.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
            <button className="ml-auto rounded-md border px-3 py-1.5 text-sm" disabled={!activeId} onClick={() => void cancelActiveConversation()}>
              Cancel conversation
            </button>
          </div>
          <div className="thread-panel flex-1">
            <Thread
              key={runtimeKey}
              welcome={{
                message: "How can I help?",
                suggestions: [
                  { prompt: "Reply with exactly: smoke ok" },
                  { prompt: "Explain inference logging in one sentence." },
                ],
              }}
              composer={{ allowAttachments: false }}
              strings={{ composer: { input: { placeholder: "Message the assistant" } } }}
            />
          </div>
        </section>
      </main>
    </AssistantRuntimeProvider>
  );
}
