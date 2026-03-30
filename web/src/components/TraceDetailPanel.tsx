"use client";

import { useEffect, useState } from "react";
import { fetchTrace } from "@/lib/api";
import type { TraceDetail } from "@/lib/types";
import { formatDate, formatLatency } from "@/lib/utils";
import StatusBadge from "./StatusBadge";
import ProviderBadge from "./ProviderBadge";
import JsonTree from "./JsonTree";

interface TraceDetailPanelProps {
  traceId: number;
}

export default function TraceDetailPanel({ traceId }: TraceDetailPanelProps) {
  const [trace, setTrace] = useState<TraceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setTrace(null);
    setError(null);
    fetchTrace(traceId)
      .then(setTrace)
      .catch((e) => setError(e.message));
  }, [traceId]);

  if (error) {
    return <p className="text-red-500 text-sm p-4">{error}</p>;
  }

  if (!trace) {
    return <p className="text-zinc-400 text-sm p-4">Loading...</p>;
  }

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <h2 className="text-lg font-semibold">Trace #{trace.id}</h2>
        <ProviderBadge provider={trace.provider} />
        <StatusBadge status={trace.status_code} />
      </div>

      {trace.error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-4 text-sm text-red-800">
          <span className="font-medium">Error: </span>
          {trace.error}
        </div>
      )}

      <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-sm mb-4 border rounded-lg p-3">
        <div>
          <span className="text-zinc-500">Request ID: </span>
          <span className="font-mono text-xs">{trace.request_id}</span>
        </div>
        <div>
          <span className="text-zinc-500">Created: </span>
          {formatDate(trace.created_at)}
        </div>
        <div>
          <span className="text-zinc-500">Provider: </span>
          {trace.provider}
        </div>
        <div>
          <span className="text-zinc-500">Model: </span>
          <span className="font-mono text-xs">{trace.model}</span>
        </div>
        <div>
          <span className="text-zinc-500">Status: </span>
          {trace.status_code ?? "—"}
        </div>
        <div>
          <span className="text-zinc-500">Latency: </span>
          {formatLatency(trace.latency_ms)}
        </div>
        {trace.idempotency_key && (
          <div>
            <span className="text-zinc-500">Idempotency Key: </span>
            <span className="font-mono text-xs">{trace.idempotency_key}</span>
          </div>
        )}
        {trace.cache_key && (
          <div>
            <span className="text-zinc-500">Cache Key: </span>
            <span className="font-mono text-xs">{trace.cache_key}</span>
          </div>
        )}
      </div>

      <div className="space-y-4">
        <div className="border rounded-lg p-3 overflow-hidden">
          <h3 className="text-sm font-medium text-zinc-500 mb-2">Request</h3>
          <div className="max-h-[400px] overflow-y-auto">
            <JsonTree data={trace.request_json} expandAll />
          </div>
        </div>
        <div className="border rounded-lg p-3 overflow-hidden">
          <h3 className="text-sm font-medium text-zinc-500 mb-2">Response</h3>
          <div className="max-h-[400px] overflow-y-auto">
            <JsonTree data={trace.response_json} expandAll />
          </div>
        </div>
      </div>
    </div>
  );
}
