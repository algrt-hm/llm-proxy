"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchLatency } from "@/lib/api";
import type { LatencyResponse } from "@/lib/types";
import LatencyScatter, {
  MIN_POINTS_FOR_PERCENTILES,
  type LatencyScale,
} from "@/components/LatencyScatter";
import ProviderBadge from "@/components/ProviderBadge";
import TimeRangeSelect from "@/components/TimeRangeSelect";
import {
  LATENCY_DOT_COLOR,
  LATENCY_ERROR_COLOR,
  PERCENTILE_LINE_COLORS,
  type TimeRangeValue,
  timeRangeToSince,
} from "@/lib/constants";

const SCALES: { label: string; value: LatencyScale }[] = [
  { label: "Log", value: "log" },
  { label: "Linear", value: "linear" },
];

// Mean and standard deviation, as a whole number in either unit.
function formatStat(ms: number): string {
  return ms < 999.5 ? `${Math.round(ms)}ms` : `${Math.round(ms / 1000)}s`;
}

export default function LatencyPage() {
  const [timeRange, setTimeRange] = useState<TimeRangeValue>("7d");
  const [scale, setScale] = useState<LatencyScale>("log");
  const [data, setData] = useState<LatencyResponse | null>(null);
  // The window the loaded data covers, so every panel shares one time axis.
  const [window, setWindow] = useState<{ since?: number; until: number } | null>(null);

  useEffect(() => {
    const since = timeRangeToSince(timeRange);
    setData(null);
    fetchLatency({ since }).then((response) => {
      setData(response);
      setWindow({ since: since ? Date.parse(since) : undefined, until: Date.now() });
    });
  }, [timeRange]);

  const xDomain = useMemo<[number, number]>(() => {
    const until = window?.until ?? Date.now();
    if (window?.since) return [window.since, until];
    // "All": start at the earliest request loaded.
    const first = Math.min(...(data?.series ?? []).map((s) => s.points[0]?.[1] ?? until), until);
    return [first, until];
  }, [data, window]);

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h1 className="text-xl font-semibold">Latency</h1>
        <div className="flex items-center gap-4">
          <div className="flex gap-1">
            {SCALES.map((option) => (
              <button
                key={option.value}
                onClick={() => setScale(option.value)}
                className={`px-3 py-1 text-xs rounded ${
                  scale === option.value
                    ? "bg-zinc-900 text-white"
                    : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200"
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
          <TimeRangeSelect value={timeRange} onChange={setTimeRange} />
        </div>
      </div>

      <p className="text-sm text-zinc-500 mb-3">
        One dot per request, by provider and model (or API endpoint). Percentiles cover every request in the
        window, errors included, and are drawn once a panel has {MIN_POINTS_FOR_PERCENTILES} requests. Click a dot
        to open its trace.
      </p>

      {/* Legend: the same encoding in every panel */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-zinc-600 mb-5">
        <span className="flex items-center gap-1.5">
          <svg width={10} height={10}>
            <circle cx={5} cy={5} r={3} fill={LATENCY_DOT_COLOR} />
          </svg>
          request
        </span>
        <span className="flex items-center gap-1.5">
          <svg width={10} height={10}>
            <path d="M2 2l6 6M2 8l6-6" stroke={LATENCY_ERROR_COLOR} strokeWidth={1.6} />
          </svg>
          error (HTTP 4xx/5xx or no response)
        </span>
        {(data?.percentiles ?? []).map((p, i) => (
          <span key={p} className="flex items-center gap-1.5">
            <svg width={18} height={10}>
              <line x1={0} x2={18} y1={5} y2={5} stroke={PERCENTILE_LINE_COLORS[i]} strokeWidth={2} />
            </svg>
            p{Math.round(p * 100)}
          </span>
        ))}
      </div>

      {data === null && <div className="text-sm text-zinc-500">Loading…</div>}
      {data !== null && data.series.length === 0 && (
        <div className="text-sm text-zinc-500">No requests in this time range.</div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        {(data?.series ?? []).map((series) => (
          <div key={`${series.provider}/${series.model}`} className="border rounded-lg p-4 min-w-0">
            <div className="flex items-center justify-between gap-3 mb-2">
              <div className="flex items-center gap-2 min-w-0">
                <ProviderBadge provider={series.provider} />
                <h3 className="text-sm font-medium text-zinc-700 truncate" title={series.model}>
                  {series.model}
                </h3>
                <span className="text-xs text-zinc-500 shrink-0">
                  μ {formatStat(series.mean)} · σ {formatStat(series.stddev)}
                </span>
              </div>
              <div className="text-xs text-zinc-500 shrink-0">
                {series.n.toLocaleString()} {series.n === 1 ? "request" : "requests"}
              </div>
            </div>
            <LatencyScatter series={series} percentiles={data!.percentiles} xDomain={xDomain} scale={scale} />
          </div>
        ))}
      </div>
    </div>
  );
}
