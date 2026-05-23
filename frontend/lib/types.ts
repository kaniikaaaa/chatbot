export type Conversation = {
  id: string;
  title: string | null;
  status: "active" | "cancelled" | "archived";
  created_at: string;
  updated_at: string;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at?: string;
};

export type Provider = "openai" | "anthropic" | "gemini";

export const PROVIDERS: Array<{ id: Provider; label: string; models: string[] }> = [
  { id: "openai", label: "OpenAI", models: ["gpt-4o-mini", "gpt-4o"] },
  { id: "anthropic", label: "Anthropic", models: ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"] },
  { id: "gemini", label: "Gemini", models: ["gemini-1.5-flash", "gemini-1.5-pro"] },
];
