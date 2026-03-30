"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { fetchTrace } from "@/lib/api";
import type { TraceDetail } from "@/lib/types";
import { formatDate, formatLatency } from "@/lib/utils";
import StatusBadge from "@/components/StatusBadge";
import ProviderBadge from "@/components/ProviderBadge";
import JsonTree from "@/components/JsonTree";

export default function TraceDetailPage() {
  const params = useParams();
  const id = Number(params.id);
  const [trace, setTrace] = useState<TraceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchTrace(id)
      .then(setTrace)
      .catch((e) => setError(e.message));
  }, [id]);

  if (error) {
    return (
      <div className="p-8 text-center">
        <p className="text-red-500 mb-4">{error}</p>
        <Link href="/traces" className="text-blue-600 hover:underline">
          Back to traces
        </Link>
      </div>
    );
  }

  if (!trace) {
    return <p className="text-zinc-400 p-8">Loading...</p>;
  }

  return (
    <div>
      <div className="mb-4">
        <Link
          href="/traces"
          className="text-sm text-blue-600 hover:underline"
        >
          &larr; Back to traces
        </Link>
      </div>

      <div className="flex items-center gap-3 mb-6">
        <h1 className="text-xl font-semibold">Trace #{trace.id}</h1>
        <ProviderBadge provider={trace.provider} />
        <StatusBadge status={trace.status_code} />
      </div>

      {trace.error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6 text-sm text-red-800">
          <span className="font-medium">Error: </span>
          {trace.error}
        </div>
      )}

      {/* Metadata */}
      <div className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm mb-6 border rounded-lg p-4">
        <div>
          <span className="text-zinc-500">Request ID: </span>
          <span className="font-mono">{trace.request_id}</span>
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
          <span className="font-mono">{trace.model}</span>
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

      {/* Request / Response side by side */}
      <div className="grid grid-cols-2 gap-4">
        <div className="border rounded-lg p-4 overflow-hidden">
          <h3 className="text-sm font-medium text-zinc-500 mb-3">Request</h3>
          <div className="max-h-[600px] overflow-y-auto">
            <JsonTree data={trace.request_json} expandAll />
          </div>
        </div>
        <div className="border rounded-lg p-4 overflow-hidden">
          <h3 className="text-sm font-medium text-zinc-500 mb-3">Response</h3>
          <div className="max-h-[600px] overflow-y-auto">
            <JsonTree data={trace.response_json} expandAll />
          </div>
        </div>
      </div>
    </div>
  );
}
