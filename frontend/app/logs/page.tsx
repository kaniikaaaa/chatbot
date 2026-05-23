"use client";

import { Fragment, useEffect, useState } from "react";
import { SideNav } from "@/components/layout/side-nav";

type Log = {
  request_id: string;
  provider: string;
  model: string;
  status: string;
  latency_ms: number | null;
  ttft_ms: number | null;
  total_tokens: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  error_message: string | null;
  input_preview: string | null;
  output_preview: string | null;
  started_at: string;
};

export default function Logs() {
  const [logs, setLogs] = useState<Log[]>([]);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      const response = await fetch("/api/ingestion/v1/logs?limit=100", { cache: "no-store" });
      setLogs(await response.json());
    }
    void load();
    const id = setInterval(load, 4000);
    return () => clearInterval(id);
  }, []);

  return (
    <main className="app-shell">
      <aside className="border-r bg-gray-50">
        <SideNav />
      </aside>
      <section className="min-h-0 overflow-auto bg-gray-50 p-5">
        <h1 className="mb-5 text-xl font-semibold">Inference Logs</h1>
        <div className="overflow-hidden rounded-lg border bg-white">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-500">
              <tr><th className="p-3">When</th><th>Provider</th><th>Model</th><th>Status</th><th>Latency</th><th>TTFT</th><th>Tokens</th><th></th></tr>
            </thead>
            <tbody>
              {logs.map((log) => (
                <Fragment key={log.request_id}>
                  <tr className="border-t">
                    <td className="p-3">{new Date(log.started_at).toLocaleTimeString()}</td>
                    <td>{log.provider}</td>
                    <td className="font-mono text-xs">{log.model}</td>
                    <td>{log.status}</td>
                    <td>{log.latency_ms ?? "-"} ms</td>
                    <td>{log.ttft_ms ?? "-"} ms</td>
                    <td>{log.total_tokens ?? "-"}</td>
                    <td><button className="text-blue-600" onClick={() => setOpen(open === log.request_id ? null : log.request_id)}>{open === log.request_id ? "hide" : "view"}</button></td>
                  </tr>
                  {open === log.request_id ? (
                    <tr className="border-t bg-gray-50">
                      <td className="p-3" colSpan={8}>
                        <div className="grid gap-4 md:grid-cols-2">
                          <pre className="whitespace-pre-wrap rounded bg-white p-3 text-xs">{log.input_preview || "(empty)"}</pre>
                          <pre className="whitespace-pre-wrap rounded bg-white p-3 text-xs">{log.output_preview || "(empty)"}</pre>
                          {log.error_message ? <pre className="whitespace-pre-wrap rounded bg-red-50 p-3 text-xs text-red-700 md:col-span-2">{log.error_message}</pre> : null}
                        </div>
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}
