"use client";

import { useEffect, useState } from "react";

type Row = {
  bucket: string; provider: string; model: string;
  calls: number; errors: number; cancelled: number;
  p50_ms: number; p95_ms: number; p99_ms: number; tokens: number;
};

function Card({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-2xl bg-ink-900/70 border border-ink-800 p-5">
      <div className="text-xs uppercase tracking-wider text-zinc-500">{label}</div>
      <div className="mt-1 text-3xl font-semibold">{value}</div>
      {sub && <div className="mt-1 text-xs text-zinc-500">{sub}</div>}
    </div>
  );
}

function MiniBars({ rows, valueKey, color }: { rows: Row[]; valueKey: keyof Row; color: string }) {
  const max = Math.max(1, ...rows.map(r => Number(r[valueKey]) || 0));
  return (
    <div className="flex items-end gap-1 h-24">
      {rows.map((r, i) => {
        const h = ((Number(r[valueKey]) || 0) / max) * 100;
        return <div key={i} title={`${r.bucket}: ${r[valueKey]}`}
          className="flex-1 min-w-[3px] rounded-t-sm" style={{ height: `${h}%`, background: color }} />;
      })}
    </div>
  );
}

export default function Dashboard() {
  const [rows, setRows] = useState<Row[]>([]);
  const [window, setWindow] = useState(60);

  useEffect(() => {
    const tick = async () => {
      const r = await fetch(`/api/ingest/v1/stats?window_minutes=${window}`);
      const j = await r.json();
      setRows(j.rows);
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => clearInterval(id);
  }, [window]);

  const totals = rows.reduce(
    (a, r) => ({
      calls: a.calls + Number(r.calls),
      errors: a.errors + Number(r.errors),
      tokens: a.tokens + Number(r.tokens || 0),
      p95: Math.max(a.p95, Number(r.p95_ms || 0)),
    }),
    { calls: 0, errors: 0, tokens: 0, p95: 0 },
  );
  const errorRate = totals.calls ? (100 * totals.errors / totals.calls).toFixed(1) : "0.0";
  const throughput = (totals.calls / Math.max(window, 1)).toFixed(2);

  // Buckets are pre-grouped by (minute, provider, model) on the server.
  // Re-bucket into single time series for the chart.
  const byBucket = new Map<string, Row>();
  for (const r of rows) {
    const cur = byBucket.get(r.bucket);
    if (cur) {
      cur.calls += Number(r.calls);
      cur.errors += Number(r.errors);
      cur.p95_ms = Math.max(cur.p95_ms, Number(r.p95_ms || 0));
      cur.tokens += Number(r.tokens || 0);
    } else {
      byBucket.set(r.bucket, { ...r,
        calls: Number(r.calls), errors: Number(r.errors),
        p95_ms: Number(r.p95_ms || 0), tokens: Number(r.tokens || 0) } as Row);
    }
  }
  const series = [...byBucket.values()].sort((a, b) => a.bucket.localeCompare(b.bucket));

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Inference Dashboard</h1>
        <select value={window} onChange={e => setWindow(Number(e.target.value))}
          className="bg-ink-800 border border-ink-700 rounded-md text-sm px-2 py-1.5">
          <option value={15}>last 15 min</option>
          <option value={60}>last 60 min</option>
          <option value={360}>last 6 h</option>
          <option value={1440}>last 24 h</option>
        </select>
      </div>

      <div className="grid grid-cols-4 gap-4">
        <Card label="Calls" value={totals.calls.toString()} sub={`${throughput}/min`} />
        <Card label="Error rate" value={`${errorRate}%`} sub={`${totals.errors} errors`} />
        <Card label="P95 latency" value={`${totals.p95} ms`} />
        <Card label="Tokens" value={totals.tokens.toLocaleString()} />
      </div>

      <div className="grid grid-cols-3 gap-4">
        <div className="rounded-2xl bg-ink-900/70 border border-ink-800 p-5">
          <div className="text-sm text-zinc-400 mb-3">Throughput (calls / min)</div>
          <MiniBars rows={series} valueKey="calls" color="#7c5cff" />
        </div>
        <div className="rounded-2xl bg-ink-900/70 border border-ink-800 p-5">
          <div className="text-sm text-zinc-400 mb-3">P95 latency (ms)</div>
          <MiniBars rows={series} valueKey="p95_ms" color="#22d3ee" />
        </div>
        <div className="rounded-2xl bg-ink-900/70 border border-ink-800 p-5">
          <div className="text-sm text-zinc-400 mb-3">Errors</div>
          <MiniBars rows={series} valueKey="errors" color="#f43f5e" />
        </div>
      </div>

      <div className="rounded-2xl bg-ink-900/70 border border-ink-800 overflow-hidden">
        <div className="px-5 py-3 text-sm text-zinc-400 border-b border-ink-800">By provider / model</div>
        <table className="w-full text-sm">
          <thead className="text-left text-zinc-500">
            <tr><th className="px-5 py-2">Provider</th><th>Model</th><th>Calls</th><th>Errors</th><th>P50</th><th>P95</th><th>P99</th><th>Tokens</th></tr>
          </thead>
          <tbody>
            {Object.values(rows.reduce((acc: any, r) => {
              const k = `${r.provider}/${r.model}`;
              if (!acc[k]) acc[k] = { ...r, calls: 0, errors: 0, tokens: 0, p50_ms: 0, p95_ms: 0, p99_ms: 0 };
              acc[k].calls += Number(r.calls);
              acc[k].errors += Number(r.errors);
              acc[k].tokens += Number(r.tokens || 0);
              acc[k].p50_ms = Math.max(acc[k].p50_ms, Number(r.p50_ms || 0));
              acc[k].p95_ms = Math.max(acc[k].p95_ms, Number(r.p95_ms || 0));
              acc[k].p99_ms = Math.max(acc[k].p99_ms, Number(r.p99_ms || 0));
              return acc;
            }, {})).map((r: any) => (
              <tr key={`${r.provider}/${r.model}`} className="border-t border-ink-800">
                <td className="px-5 py-2">{r.provider}</td>
                <td>{r.model}</td>
                <td>{r.calls}</td>
                <td>{r.errors}</td>
                <td>{r.p50_ms}</td>
                <td>{r.p95_ms}</td>
                <td>{r.p99_ms}</td>
                <td>{Number(r.tokens || 0).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
