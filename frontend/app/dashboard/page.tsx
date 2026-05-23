"use client";

import { useEffect, useState } from "react";
import { SideNav } from "@/components/layout/side-nav";

type Row = {
  bucket: string;
  provider: string;
  model: string;
  calls: number;
  errors: number;
  cancelled: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  tokens: number | null;
};

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg border bg-white p-4">
      <div className="text-xs uppercase tracking-wide text-gray-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
      {sub ? <div className="mt-1 text-xs text-gray-500">{sub}</div> : null}
    </div>
  );
}

function Bars({ rows, field }: { rows: Row[]; field: keyof Row }) {
  const max = Math.max(1, ...rows.map((row) => Number(row[field]) || 0));
  return (
    <div className="flex h-24 items-end gap-1">
      {rows.map((row) => (
        <div key={`${row.bucket}-${row.provider}-${row.model}`} className="min-w-1 flex-1 rounded-t bg-gray-900" style={{ height: `${((Number(row[field]) || 0) / max) * 100}%` }} />
      ))}
    </div>
  );
}

export default function Dashboard() {
  const [rows, setRows] = useState<Row[]>([]);
  const [windowMinutes, setWindowMinutes] = useState(60);

  useEffect(() => {
    async function load() {
      const response = await fetch(`/api/ingestion/v1/stats?window_minutes=${windowMinutes}`, { cache: "no-store" });
      const data = await response.json();
      setRows(data.rows || []);
    }
    void load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [windowMinutes]);

  const totals = rows.reduce((acc, row) => ({
    calls: acc.calls + Number(row.calls),
    errors: acc.errors + Number(row.errors),
    tokens: acc.tokens + Number(row.tokens || 0),
    p95: Math.max(acc.p95, Number(row.p95_ms || 0)),
  }), { calls: 0, errors: 0, tokens: 0, p95: 0 });

  return (
    <main className="app-shell">
      <aside className="border-r bg-gray-50">
        <SideNav />
      </aside>
      <section className="min-h-0 overflow-auto bg-gray-50 p-5">
        <div className="mb-5 flex items-center justify-between">
          <h1 className="text-xl font-semibold">Inference Dashboard</h1>
          <select className="rounded-md border bg-white px-2 py-1.5 text-sm" value={windowMinutes} onChange={(event) => setWindowMinutes(Number(event.target.value))}>
            <option value={15}>last 15 min</option>
            <option value={60}>last 60 min</option>
            <option value={360}>last 6 h</option>
            <option value={1440}>last 24 h</option>
          </select>
        </div>
        <div className="grid gap-4 md:grid-cols-4">
          <Stat label="Calls" value={String(totals.calls)} sub={`${(totals.calls / windowMinutes).toFixed(2)}/min`} />
          <Stat label="Error rate" value={`${totals.calls ? ((100 * totals.errors) / totals.calls).toFixed(1) : "0.0"}%`} sub={`${totals.errors} errors`} />
          <Stat label="P95 latency" value={`${totals.p95} ms`} />
          <Stat label="Tokens" value={totals.tokens.toLocaleString()} />
        </div>
        <div className="mt-4 grid gap-4 md:grid-cols-3">
          <div className="rounded-lg border bg-white p-4"><div className="mb-3 text-sm text-gray-500">Throughput</div><Bars rows={rows} field="calls" /></div>
          <div className="rounded-lg border bg-white p-4"><div className="mb-3 text-sm text-gray-500">P95 latency</div><Bars rows={rows} field="p95_ms" /></div>
          <div className="rounded-lg border bg-white p-4"><div className="mb-3 text-sm text-gray-500">Errors</div><Bars rows={rows} field="errors" /></div>
        </div>
        <div className="mt-4 overflow-hidden rounded-lg border bg-white">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-500">
              <tr><th className="p-3">Provider</th><th>Model</th><th>Calls</th><th>Errors</th><th>P50</th><th>P95</th><th>P99</th><th>Tokens</th></tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr className="border-t" key={`${row.bucket}-${row.provider}-${row.model}`}>
                  <td className="p-3">{row.provider}</td><td>{row.model}</td><td>{row.calls}</td><td>{row.errors}</td><td>{row.p50_ms}</td><td>{row.p95_ms}</td><td>{row.p99_ms}</td><td>{row.tokens ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}
