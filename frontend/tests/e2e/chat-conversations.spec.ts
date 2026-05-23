import { expect, test } from "@playwright/test";

test("new chat creates isolated thread and resume/cancel works", async ({ page }) => {
  let conversationCounter = 0;
  let messageCounter = 0;
  const conversations: Array<{
    id: string;
    title: string | null;
    status: "active" | "cancelled";
    created_at: string;
    updated_at: string;
  }> = [];
  const messagesByConversation = new Map<string, Array<{ id: string; role: "user" | "assistant"; content: string; created_at: string }>>();
  const now = () => new Date().toISOString();

  await page.route("**/api/chat/v1/**", async (route) => {
    const request = route.request();
    const method = request.method();
    const url = new URL(request.url());
    const path = url.pathname;

    if (method === "GET" && path === "/api/chat/v1/conversations") {
      const sorted = [...conversations].sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(sorted) });
      return;
    }

    if (method === "POST" && path === "/api/chat/v1/conversations") {
      const payload = request.postDataJSON() as { title?: string };
      conversationCounter += 1;
      const conversation = {
        id: `conv-${conversationCounter}`,
        title: payload.title ?? null,
        status: "active" as const,
        created_at: now(),
        updated_at: now(),
      };
      conversations.unshift(conversation);
      messagesByConversation.set(conversation.id, []);
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(conversation) });
      return;
    }

    const messageMatch = path.match(/^\/api\/chat\/v1\/conversations\/([^/]+)\/messages$/);
    if (method === "GET" && messageMatch) {
      const cid = messageMatch[1];
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(messagesByConversation.get(cid) ?? []),
      });
      return;
    }

    const cancelMatch = path.match(/^\/api\/chat\/v1\/conversations\/([^/]+)\/cancel$/);
    if (method === "POST" && cancelMatch) {
      const cid = cancelMatch[1];
      const item = conversations.find((entry) => entry.id === cid);
      if (item) {
        item.status = "cancelled";
        item.updated_at = now();
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, cancelled_inflight: false }) });
      return;
    }

    const deleteMatch = path.match(/^\/api\/chat\/v1\/conversations\/([^/]+)$/);
    if (method === "DELETE" && deleteMatch) {
      const cid = deleteMatch[1];
      const index = conversations.findIndex((entry) => entry.id === cid);
      if (index >= 0) conversations.splice(index, 1);
      messagesByConversation.delete(cid);
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
      return;
    }

    if (method === "POST" && path === "/api/chat/v1/chat") {
      const payload = request.postDataJSON() as { conversation_id: string; content: string; stream: boolean };
      const cid = payload.conversation_id;
      const thread = messagesByConversation.get(cid) ?? [];
      const conversation = conversations.find((entry) => entry.id === cid);
      const reply = payload.content.replace(/^Reply with exactly:\s*/i, "").trim();

      messageCounter += 1;
      thread.push({ id: `msg-${messageCounter}`, role: "user", content: payload.content, created_at: now() });
      messageCounter += 1;
      thread.push({ id: `msg-${messageCounter}`, role: "assistant", content: reply, created_at: now() });
      messagesByConversation.set(cid, thread);
      if (conversation) {
        conversation.status = "active";
        conversation.updated_at = now();
      }

      if (payload.stream) {
        const body = [
          `data: ${JSON.stringify({ type: "start", request_id: `req-${messageCounter}` })}`,
          "",
          `data: ${JSON.stringify({ type: "delta", content: reply })}`,
          "",
          `data: ${JSON.stringify({ type: "end" })}`,
          "",
          `data: ${JSON.stringify({ type: "saved", message_id: `msg-${messageCounter}` })}`,
          "",
        ].join("\n");
        await route.fulfill({ status: 200, contentType: "text/event-stream", body });
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ request_id: `req-${messageCounter}`, message_id: `msg-${messageCounter}`, content: reply }),
      });
      return;
    }

    await route.abort();
  });

  const markerOne = `e2e-newchat-one-${Date.now()}`;
  const markerTwo = `e2e-newchat-two-${Date.now()}`;

  await page.goto("/");
  await expect(page.locator("aside .text-sm.font-semibold")).toHaveText("Conversations");

  const newButton = page.getByRole("button", { name: /^New$/ });
  const threadPanel = page.locator(".thread-panel");
  const sendPrompt = async (marker: string) => {
    const prompt = `Reply with exactly: ${marker}`;
    const composer = page.locator('textarea[placeholder="Message the assistant"]');
    const sendButton = page.locator("button.aui-composer-send");
    await expect(composer).toBeVisible();
    await composer.fill(prompt);
    await expect(composer).toHaveValue(prompt);
    await expect(sendButton).toBeEnabled();
    await sendButton.click();
  };

  await newButton.click();
  await expect(page.locator("aside .space-y-1 > div").first()).toBeVisible();

  await sendPrompt(markerOne);
  await expect(threadPanel).toContainText(markerOne, { timeout: 120_000 });

  await newButton.click();
  await expect(threadPanel).not.toContainText(markerOne);

  await sendPrompt(markerTwo);
  await expect(threadPanel).toContainText(markerTwo, { timeout: 120_000 });

  const conversationRows = page.locator("aside .space-y-1 > div");
  const rowCount = await conversationRows.count();
  let resumed = false;
  for (let i = 0; i < rowCount; i += 1) {
    await conversationRows.nth(i).locator("button.block").click();
    const containsFirstMarker = await expect(threadPanel)
      .toContainText(markerOne, { timeout: 15_000 })
      .then(() => true)
      .catch(() => false);
    if (containsFirstMarker) {
      resumed = true;
      break;
    }
  }
  expect(resumed).toBeTruthy();

  const cancelButton = page.getByRole("button", { name: /Cancel conversation/i });
  await cancelButton.click();
  await expect(page.locator("aside .space-y-1 > div.border-gray-900")).toContainText("cancelled");
});
