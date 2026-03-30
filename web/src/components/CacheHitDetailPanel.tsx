"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchTrace } from "@/lib/api";
import type { CacheHitItem, TraceDetail } from "@/lib/types";
import { formatDate } from "@/lib/utils";
import ProviderBadge from "./ProviderBadge";
import JsonTree from "./JsonTree";

interface CacheHitDetailPanelProps {
  cacheHit: CacheHitItem;
}

export default function CacheHitDetailPanel({ cacheHit }: CacheHitDetailPanelProps) {
  const [trace, setTrace] = useState<TraceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setTrace(null);
    setError(null);
    fetchTrace(cacheHit.cached_trace_id)
      .then(setTrace)
      .catch((e) => setError(e.message));
  }, [cacheHit.cached_trace_id]);

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <h2 className="text-lg font-semibold">Cache Hit #{cacheHit.id}</h2>
        <ProviderBadge provider={cacheHit.provider} />
        <span
          className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
            cacheHit.cache_type === "response"
              ? "bg-blue-100 text-blue-800"
              : "bg-purple-100 text-purple-800"
          }`}
        >
          {cacheHit.cache_type}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-sm mb-4 border rounded-lg p-3">
        <div>
          <span className="text-zinc-500">ID: </span>
          <span className="font-mono">{cacheHit.id}</span>
        </div>
        <div>
          <span className="text-zinc-500">Time: </span>
          {formatDate(cacheHit.created_at)}
        </div>
        <div>
          <span className="text-zinc-500">Provider: </span>
          {cacheHit.provider}
        </div>
        <div>
          <span className="text-zinc-500">Model: </span>
          <span className="font-mono text-xs">{cacheHit.model}</span>
        </div>
        <div>
          <span className="text-zinc-500">Cache Type: </span>
          {cacheHit.cache_type}
        </div>
        <div>
          <span className="text-zinc-500">Cached Trace: </span>
          <Link
            href={`/traces/${cacheHit.cached_trace_id}`}
            className="text-blue-600 hover:underline font-mono"
          >
            #{cacheHit.cached_trace_id}
          </Link>
        </div>
      </div>

      {error && (
        <p className="text-red-500 text-sm mb-4">Failed to load trace: {error}</p>
      )}

      {!trace && !error && (
        <p className="text-zinc-400 text-sm">Loading trace...</p>
      )}

      {trace && (
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
      )}
    </div>
  );
}
