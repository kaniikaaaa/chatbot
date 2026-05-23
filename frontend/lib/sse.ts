export type ChatEvent =
  | { type: "start"; request_id: string }
  | { type: "delta"; content: string }
  | { type: "end" }
  | { type: "saved"; message_id: string }
  | { type: "cancelled" }
  | { type: "error"; message: string };

export async function* parseSse(response: Response): AsyncGenerator<ChatEvent> {
  if (!response.ok) {
    throw new Error(await response.text());
  }
  if (!response.body) throw new Error("empty response body");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.split("\n").find((item) => item.startsWith("data: "));
      if (line) yield JSON.parse(line.slice(6)) as ChatEvent;
    }
  }
}
