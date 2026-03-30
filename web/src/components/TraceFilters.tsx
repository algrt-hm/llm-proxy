"use client";

import { useEffect, useState } from "react";
import { fetchProviders } from "@/lib/api";
import TimeRangeSelect from "./TimeRangeSelect";
import type { TimeRangeValue } from "@/lib/constants";

interface TraceFiltersProps {
  provider: string;
  model: string;
  statusFilter: string;
  timeRange: TimeRangeValue;
  excludeEmbeddings: boolean;
  onProviderChange: (v: string) => void;
  onModelChange: (v: string) => void;
  onStatusChange: (v: string) => void;
  onTimeRangeChange: (v: TimeRangeValue) => void;
  onExcludeEmbeddingsChange: (v: boolean) => void;
}

export default function TraceFilters({
  provider,
  model,
  statusFilter,
  timeRange,
  onProviderChange,
  onModelChange,
  onStatusChange,
  excludeEmbeddings,
  onTimeRangeChange,
  onExcludeEmbeddingsChange,
}: TraceFiltersProps) {
  const [providers, setProviders] = useState<Record<string, string[]>>({});

  useEffect(() => {
    fetchProviders().then((d) => setProviders(d.providers));
  }, []);

  const models = provider ? providers[provider] || [] : [];

  return (
    <div className="flex flex-wrap items-center gap-3 mb-4">
      <select
        value={provider}
        onChange={(e) => {
          onProviderChange(e.target.value);
          onModelChange("");
        }}
        className="border rounded px-2 py-1 text-sm bg-white"
      >
        <option value="">All providers</option>
        {Object.keys(providers).map((p) => (
          <option key={p} value={p}>
            {p}
          </option>
        ))}
      </select>

      <select
        value={model}
        onChange={(e) => onModelChange(e.target.value)}
        disabled={!provider}
        className="border rounded px-2 py-1 text-sm bg-white disabled:opacity-40"
      >
        <option value="">All models</option>
        {models.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>

      <select
        value={statusFilter}
        onChange={(e) => onStatusChange(e.target.value)}
        className="border rounded px-2 py-1 text-sm bg-white"
      >
        <option value="">All statuses</option>
        <option value="ok">OK (2xx)</option>
        <option value="error">Error</option>
      </select>

      <TimeRangeSelect value={timeRange} onChange={onTimeRangeChange} />

      <label className="flex items-center gap-1.5 text-sm text-zinc-600 cursor-pointer select-none">
        <input
          type="checkbox"
          checked={excludeEmbeddings}
          onChange={(e) => onExcludeEmbeddingsChange(e.target.checked)}
          className="rounded"
        />
        Hide embeddings
      </label>
    </div>
  );
}
