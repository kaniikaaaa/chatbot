"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Conv = { id: string; title: string | null; status: string; updated_at: string };
type Msg = { id: string; role: "user" | "assistant" | "system"; content: string; created_at?: string };

const PROVIDERS = [
  { id: "openai",    label: "OpenAI",    models: ["gpt-4o-mini", "gpt-4o"] },
  { id: "anthropic", label: "Anthropic", models: ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"] },
  { id: "gemini",    label: "Gemini",    models: ["gemini-1.5-flash", "gemini-1.5-pro"] },
];

export default function ChatPage() {
  const [conversations, setConversations] = useState<Conv[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [provider, setProvider] = useState("openai");
  const [model, setModel] = useState(PROVIDERS[0].models[0]);
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const scroller = useRef<HTMLDivElement>(null);

  const refreshList = useCallback(async () => {
    const r = await fetch("/api/chat/v1/conversations");
    setConversations(await r.json());
  }, []);

  const loadMessages = useCallback(async (id: string) => {
    const r = await fetch(`/api/chat/v1/conversations/${id}/messages`);
    setMessages(await r.json());
  }, []);

  useEffect(() => { refreshList(); }, [refreshList]);
  useEffect(() => { if (activeId) loadMessages(activeId); }, [activeId, loadMessages]);
  useEffect(() => { scroller.current?.scrollTo({ top: scroller.current.scrollHeight }); }, [messages]);

  async function newConversation() {
    const r = await fetch("/api/chat/v1/conversations", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ title: "New chat" }),
    });
    const c: Conv = await r.json();
    setActiveId(c.id);
    setMessages([]);
    refreshList();
  }

  async function cancelActive() {
    if (!activeId) return;
    abortRef.current?.abort();
    await fetch(`/api/chat/v1/conversations/${activeId}/cancel`, { method: "POST" });
    setStreaming(false);
    refreshList();
  }

  async function deleteConv(id: string) {
    await fetch(`/api/chat/v1/conversations/${id}`, { method: "DELETE" });
    if (id === activeId) { setActiveId(null); setMessages([]); }
    refreshList();
  }

  async function send() {
    if (!input.trim()) return;
    let cid = activeId;
    if (!cid) {
      const r = await fetch("/api/chat/v1/conversations", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ title: input.slice(0, 40) }),
      });
      cid = (await r.json()).id;
      setActiveId(cid);
    }
    const userMsg: Msg = { id: crypto.randomUUID(), role: "user", content: input };
    const assistantMsg: Msg = { id: crypto.randomUUID(), role: "assistant", content: "" };
    setMessages(m => [...m, userMsg, assistantMsg]);
    setInput("");
    setStreaming(true);

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      const resp = await fetch("/api/chat/v1/chat", {
        method: "POST", headers: { "content-type": "application/json" },
        signal: ctrl.signal,
        body: JSON.stringify({ conversation_id: cid, content: userMsg.content, provider, model, stream: true }),
      });
      if (!resp.body) throw new Error("no body");
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() || "";
        for (const part of parts) {
          if (!part.startsWith("data: ")) continue;
          const ev = JSON.parse(part.slice(6));
          if (ev.type === "delta") {
            setMessages(m => {
              const copy = [...m];
              copy[copy.length - 1] = { ...copy[copy.length - 1], content: copy[copy.length - 1].content + ev.content };
              return copy;
            });
          }
        }
      }
    } catch (err) {
      if ((err as any).name !== "AbortError") console.error(err);
    } finally {
      setStreaming(false);
      abortRef.current = null;
      refreshList();
    }
  }

  return (
    <div className="grid grid-cols-12 gap-6 h-[calc(100vh-7rem)]">
      <aside className="col-span-3 rounded-2xl bg-ink-900/70 border border-ink-800 p-3 flex flex-col">
        <button onClick={newConversation}
          className="rounded-lg bg-accent/90 hover:bg-accent text-white text-sm font-medium px-3 py-2 mb-3 transition">
          + New conversation
        </button>
        <div className="text-xs uppercase tracking-wider text-zinc-500 px-2 mb-1">Conversations</div>
        <div className="flex-1 overflow-y-auto scrollbar-thin pr-1">
          {conversations.length === 0 && <div className="text-sm text-zinc-500 px-2 py-4">No conversations yet.</div>}
          {conversations.map(c => (
            <div key={c.id}
              className={"group flex items-center justify-between rounded-lg px-2 py-2 mb-1 cursor-pointer transition " +
                (c.id === activeId ? "bg-ink-800" : "hover:bg-ink-800/60")}
              onClick={() => setActiveId(c.id)}>
              <div className="min-w-0">
                <div className="text-sm truncate">{c.title || "Untitled"}</div>
                <div className="text-[11px] text-zinc-500 flex items-center gap-1.5">
                  <span className={"size-1.5 rounded-full " +
                    (c.status === "active" ? "bg-emerald-400" : c.status === "cancelled" ? "bg-amber-400" : "bg-zinc-500")} />
                  {c.status}
                </div>
              </div>
              <button onClick={(e) => { e.stopPropagation(); deleteConv(c.id); }}
                className="opacity-0 group-hover:opacity-100 text-xs text-zinc-400 hover:text-red-400 px-1 transition">×</button>
            </div>
          ))}
        </div>
      </aside>

      <section className="col-span-9 rounded-2xl bg-ink-900/70 border border-ink-800 flex flex-col overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-ink-800">
          <select value={provider}
            onChange={e => { setProvider(e.target.value); setModel(PROVIDERS.find(p => p.id === e.target.value)!.models[0]); }}
            className="bg-ink-800 border border-ink-700 rounded-md text-sm px-2 py-1.5">
            {PROVIDERS.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
          <select value={model} onChange={e => setModel(e.target.value)}
            className="bg-ink-800 border border-ink-700 rounded-md text-sm px-2 py-1.5">
            {PROVIDERS.find(p => p.id === provider)!.models.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
          <div className="ml-auto text-xs text-zinc-500">
            {activeId ? <>conversation <span className="font-mono">{activeId.slice(0, 8)}</span></> : "no conversation"}
          </div>
        </div>

        <div ref={scroller} className="flex-1 overflow-y-auto scrollbar-thin px-6 py-5 space-y-4">
          {messages.length === 0 && (
            <div className="text-zinc-500 text-sm flex flex-col items-center justify-center h-full">
              <div className="text-lg mb-1">Start a conversation</div>
              <div>Pick a provider above and send a message.</div>
            </div>
          )}
          {messages.map(m => (
            <div key={m.id} className={"fade-in flex " + (m.role === "user" ? "justify-end" : "justify-start")}>
              <div className={"max-w-[80%] rounded-2xl px-4 py-2.5 text-[15px] leading-relaxed whitespace-pre-wrap " +
                (m.role === "user" ? "bg-accent/90 text-white rounded-br-md" : "bg-ink-800 text-zinc-100 rounded-bl-md")}>
                {m.content || <span className="inline-flex gap-1">
                  <span className="size-1.5 rounded-full bg-zinc-500 animate-pulse" />
                  <span className="size-1.5 rounded-full bg-zinc-500 animate-pulse [animation-delay:150ms]" />
                  <span className="size-1.5 rounded-full bg-zinc-500 animate-pulse [animation-delay:300ms]" />
                </span>}
              </div>
            </div>
          ))}
        </div>

        <div className="border-t border-ink-800 p-3 flex items-end gap-2">
          <textarea value={input} onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
            rows={1} placeholder="Send a message…"
            className="flex-1 resize-none bg-ink-800 border border-ink-700 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-accent/50 max-h-40" />
          {streaming
            ? <button onClick={cancelActive}
                className="rounded-xl bg-red-500/90 hover:bg-red-500 text-white text-sm font-medium px-4 py-2.5">Cancel</button>
            : <button onClick={send} disabled={!input.trim()}
                className="rounded-xl bg-accent/90 hover:bg-accent disabled:opacity-40 text-white text-sm font-medium px-4 py-2.5">Send</button>}
        </div>
      </section>
    </div>
  );
}
