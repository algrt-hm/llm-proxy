"use client";

import { useEffect, useState } from "react";
import { fetchStats, fetchCacheHitStats } from "@/lib/api";
import type { StatsResponse, CacheHitStatsResponse } from "@/lib/types";
import { formatLatency, formatTokens, successRate } from "@/lib/utils";
import TimeRangeSelect from "@/components/TimeRangeSelect";
import StatsTable from "@/components/StatsTable";
import BarChart from "@/components/BarChart";
import { type TimeRangeValue, timeRangeToSince } from "@/lib/constants";

export default function DashboardPage() {
  const [timeRange, setTimeRange] = useState<TimeRangeValue>("24h");
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [cacheStats, setCacheStats] = useState<CacheHitStatsResponse | null>(
    null
  );

  useEffect(() => {
    const since = timeRangeToSince(timeRange);
    fetchStats({ since }).then(setStats);
    fetchCacheHitStats({ since }).then(setCacheStats);
  }, [timeRange]);

  const totals = stats?.totals;

  const tracesByProvider = stats
    ? Object.entries(
        stats.by_provider_model.reduce(
          (acc, s) => {
            acc[s.provider] = (acc[s.provider] || 0) + s.traces;
            return acc;
          },
          {} as Record<string, number>
        )
      )
        .map(([label, value]) => ({ label, value, provider: label }))
        .sort((a, b) => b.value - a.value)
    : [];

  const latencyByProvider = stats
    ? Object.entries(
        stats.by_provider_model.reduce(
          (acc, s) => {
            if (!acc[s.provider]) acc[s.provider] = { total: 0, count: 0 };
            acc[s.provider].total += s.avg_ms * s.traces;
            acc[s.provider].count += s.traces;
            return acc;
          },
          {} as Record<string, { total: number; count: number }>
        )
      )
        .map(([label, v]) => ({
          label,
          value: Math.round(v.total / v.count),
          provider: label,
        }))
        .sort((a, b) => b.value - a.value)
    : [];

  const totalCacheHits = cacheStats
    ? cacheStats.items.reduce((sum, i) => sum + i.cache_hits, 0)
    : 0;
  const totalCacheTraces = cacheStats
    ? cacheStats.items.reduce((sum, i) => sum + i.traces + i.cache_hits, 0)
    : 0;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-semibold">Dashboard</h1>
        <TimeRangeSelect value={timeRange} onChange={setTimeRange} />
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-4 gap-4 mb-8">
        <div className="border rounded-lg p-4">
          <div className="text-xs text-zinc-500 mb-1">Total Traces</div>
          <div className="text-2xl font-semibold">
            {totals ? totals.traces.toLocaleString() : "—"}
          </div>
        </div>
        <div className="border rounded-lg p-4">
          <div className="text-xs text-zinc-500 mb-1">Avg Latency</div>
          <div className="text-2xl font-semibold">
            {totals ? formatLatency(totals.avg_ms) : "—"}
          </div>
        </div>
        <div className="border rounded-lg p-4">
          <div className="text-xs text-zinc-500 mb-1">Success Rate</div>
          <div className="text-2xl font-semibold">
            {totals ? successRate(totals.ok, totals.traces) : "—"}
          </div>
        </div>
        <div className="border rounded-lg p-4">
          <div className="text-xs text-zinc-500 mb-1">Cache Hit Rate</div>
          <div className="text-2xl font-semibold">
            {totalCacheTraces > 0
              ? `${((totalCacheHits / totalCacheTraces) * 100).toFixed(1)}%`
              : "—"}
          </div>
        </div>
      </div>

      {/* Charts */}
      <div className="grid grid-cols-2 gap-6 mb-8">
        <div className="border rounded-lg p-4">
          <h3 className="text-sm font-medium text-zinc-500 mb-3">
            Traces by Provider
          </h3>
          <BarChart data={tracesByProvider} />
        </div>
        <div className="border rounded-lg p-4">
          <h3 className="text-sm font-medium text-zinc-500 mb-3">
            Avg Latency by Provider (s)
          </h3>
          <BarChart data={latencyByProvider} formatValue={(ms) => (ms / 1000).toFixed(1) + "s"} />
        </div>
      </div>

      {/* Stats table */}
      <div className="border rounded-lg p-4 mb-8">
        <h3 className="text-sm font-medium text-zinc-500 mb-3">
          By Provider / Model
        </h3>
        <StatsTable stats={stats?.by_provider_model || []} />
      </div>

      {/* Token summary */}
      {totals && (
        <div className="border rounded-lg p-4 mb-8">
          <h3 className="text-sm font-medium text-zinc-500 mb-3">
            Token Usage
          </h3>
          <div className="grid grid-cols-4 gap-4 text-sm">
            <div>
              <span className="text-zinc-500">Input: </span>
              <span className="font-medium">
                {formatTokens(totals.tok_in)}
              </span>
            </div>
            <div>
              <span className="text-zinc-500">Output: </span>
              <span className="font-medium">
                {formatTokens(totals.tok_out)}
              </span>
            </div>
            <div>
              <span className="text-zinc-500">Thinking: </span>
              <span className="font-medium">
                {formatTokens(totals.tok_think)}
              </span>
            </div>
            <div>
              <span className="text-zinc-500">Total: </span>
              <span className="font-medium">
                {formatTokens(totals.tok_total)}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Cache hit rates */}
      {cacheStats && cacheStats.items.length > 0 && (
        <div className="border rounded-lg p-4">
          <h3 className="text-sm font-medium text-zinc-500 mb-3">
            Cache Hit Rates
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-zinc-500">
                  <th className="py-2 pr-3 font-medium">Provider</th>
                  <th className="py-2 pr-3 font-medium">Model</th>
                  <th className="py-2 pr-3 font-medium text-right">Traces</th>
                  <th className="py-2 pr-3 font-medium text-right">
                    Cache Hits
                  </th>
                  <th className="py-2 pr-3 font-medium text-right">Hit %</th>
                </tr>
              </thead>
              <tbody>
                {cacheStats.items.map((c) => (
                  <tr
                    key={`${c.provider}-${c.model}`}
                    className="border-b border-zinc-100"
                  >
                    <td className="py-2 pr-3">{c.provider}</td>
                    <td className="py-2 pr-3 font-mono text-xs">{c.model}</td>
                    <td className="py-2 pr-3 text-right">{c.traces}</td>
                    <td className="py-2 pr-3 text-right">{c.cache_hits}</td>
                    <td className="py-2 pr-3 text-right">{c.hit_pct}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
