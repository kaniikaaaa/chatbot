import { NextRequest } from "next/server";

const TARGET = process.env.CHATBOT_API_URL || "http://chatbot-api:8000";

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const url = new URL(request.url);
  const target = `${TARGET}/${path.join("/")}${url.search}`;
  const headers = new Headers(request.headers);
  headers.set("authorization", `Bearer ${process.env.API_AUTH_TOKEN || ""}`);
  headers.delete("host");
  return fetch(target, {
    method: request.method,
    headers,
    body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
    duplex: "half",
  } as RequestInit & { duplex: "half" });
}

export const GET = proxy;
export const POST = proxy;
export const DELETE = proxy;
