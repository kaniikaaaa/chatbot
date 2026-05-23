import type { ChatMessage, Conversation } from "./types";

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<T>;
}

export async function listConversations() {
  return json<Conversation[]>(await fetch("/api/chat/v1/conversations", { cache: "no-store" }));
}

export async function createConversation(title: string) {
  return json<Conversation>(await fetch("/api/chat/v1/conversations", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ title }),
  }));
}

export async function deleteConversation(id: string) {
  await json(await fetch(`/api/chat/v1/conversations/${id}`, { method: "DELETE" }));
}

export async function cancelConversation(id: string) {
  await json(await fetch(`/api/chat/v1/conversations/${id}/cancel`, { method: "POST" }));
}

export async function listMessages(id: string) {
  return json<ChatMessage[]>(await fetch(`/api/chat/v1/conversations/${id}/messages`, { cache: "no-store" }));
}
