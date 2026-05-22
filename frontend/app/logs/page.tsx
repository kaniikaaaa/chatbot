"use client";

import { Fragment, useEffect, useState } from "react";

type Log = {
  request_id: string; provider: string; model: string; status: string;
  latency_ms: number | null; ttft_ms: number | null;
  prompt_tokens: number | null; completion_tokens: number | null; total_tokens: number | null;
  error_message: string | null; input_preview: string | null; output_preview: string | null;
  started_at: string;
};

export default function Logs() {
  const [logs, setLogs] = useState<Log[]>([]);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    const tick = async () => {
      const r = await fetch("/api/ingest/v1/logs?limit=100");
      setLogs(await r.json());
    };
    tick();
    const id = setInterval(tick, 4000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Recent inference logs</h1>
      <div className="rounded-2xl bg-ink-900/70 border border-ink-800 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="text-left text-zinc-500">
            <tr>
              <th className="px-4 py-2">When</th><th>Provider</th><th>Model</th>
              <th>Status</th><th>Latency</th><th>TTFT</th><th>Tokens</th><th></th>
            </tr>
          </thead>
          <tbody>
            {logs.map(l => (
              <Fragment key={l.request_id}>
                <tr className="border-t border-ink-800 hover:bg-ink-800/40">
                  <td className="px-4 py-2 text-zinc-400">{new Date(l.started_at).toLocaleTimeString()}</td>
                  <td>{l.provider}</td>
                  <td className="font-mono text-xs">{l.model}</td>
                  <td>
                    <span className={"inline-block px-1.5 py-0.5 rounded text-xs " +
                      (l.status === "success" ? "bg-emerald-500/20 text-emerald-300" :
                       l.status === "cancelled" ? "bg-amber-500/20 text-amber-300" :
                       "bg-red-500/20 text-red-300")}>{l.status}</span>
                  </td>
                  <td>{l.latency_ms ?? "-"} ms</td>
                  <td>{l.ttft_ms ?? "-"} ms</td>
                  <td>{l.total_tokens ?? "-"}</td>
                  <td>
                    <button onClick={() => setOpen(open === l.request_id ? null : l.request_id)}
                      className="text-xs text-accent-soft hover:underline">{open === l.request_id ? "hide" : "view"}</button>
                  </td>
                </tr>
                {open === l.request_id && (
                  <tr className="bg-ink-950/60">
                    <td colSpan={8} className="px-4 py-3">
                      <div className="grid grid-cols-2 gap-4 text-xs">
                        <div>
                          <div className="text-zinc-500 mb-1">Input preview</div>
                          <pre className="whitespace-pre-wrap font-mono text-zinc-300">{l.input_preview || "(empty)"}</pre>
                        </div>
                        <div>
                          <div className="text-zinc-500 mb-1">Output preview</div>
                          <pre className="whitespace-pre-wrap font-mono text-zinc-300">{l.output_preview || "(empty)"}</pre>
                        </div>
                        {l.error_message && (
                          <div className="col-span-2">
                            <div className="text-zinc-500 mb-1">Error</div>
                            <pre className="whitespace-pre-wrap text-red-300">{l.error_message}</pre>
                          </div>
                        )}
                        <div className="col-span-2 text-zinc-500">
                          request_id <span className="font-mono">{l.request_id}</span> · prompt {l.prompt_tokens ?? "?"} / completion {l.completion_tokens ?? "?"}
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
