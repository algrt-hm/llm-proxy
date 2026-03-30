"use client";

import { Suspense, useEffect, useState, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { fetchTraces } from "@/lib/api";
import type { TraceListItem } from "@/lib/types";
import TraceFilters from "@/components/TraceFilters";
import TraceTable from "@/components/TraceTable";
import TraceDetailPanel from "@/components/TraceDetailPanel";
import Pagination from "@/components/Pagination";
import { type TimeRangeValue, timeRangeToSince } from "@/lib/constants";

const PAGE_SIZE = 50;

function TracesContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const provider = searchParams.get("provider") || "";
  const model = searchParams.get("model") || "";
  const statusFilter = searchParams.get("status") || "";
  const timeRange = (searchParams.get("range") || "24h") as TimeRangeValue;
  const excludeEmbeddings = searchParams.get("hide_emb") !== "0";
  const offset = parseInt(searchParams.get("offset") || "0", 10);

  const [traces, setTraces] = useState<TraceListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const updateParams = useCallback(
    (updates: Record<string, string>) => {
      const params = new URLSearchParams(searchParams.toString());
      for (const [key, value] of Object.entries(updates)) {
        if (value) {
          params.set(key, value);
        } else {
          params.delete(key);
        }
      }
      router.push(`/traces?${params.toString()}`);
    },
    [router, searchParams]
  );

  useEffect(() => {
    const since = timeRangeToSince(timeRange);
    fetchTraces({
      limit: PAGE_SIZE,
      offset,
      provider: provider || undefined,
      model: model || undefined,
      status_filter: statusFilter || undefined,
      exclude_embeddings: excludeEmbeddings ? "1" : undefined,
      since,
    }).then((data) => {
      setTraces(data.items);
      setTotal(data.total);
    });
  }, [provider, model, statusFilter, timeRange, excludeEmbeddings, offset]);

  return (
    <div>
      <h1 className="text-xl font-semibold mb-4">Traces</h1>
      <TraceFilters
        provider={provider}
        model={model}
        statusFilter={statusFilter}
        timeRange={timeRange}
        onProviderChange={(v) => updateParams({ provider: v, model: "", offset: "0" })}
        onModelChange={(v) => updateParams({ model: v, offset: "0" })}
        onStatusChange={(v) => updateParams({ status: v, offset: "0" })}
        excludeEmbeddings={excludeEmbeddings}
        onTimeRangeChange={(v) => updateParams({ range: v, offset: "0" })}
        onExcludeEmbeddingsChange={(v) => updateParams({ hide_emb: v ? "" : "0", offset: "0" })}
      />
      <div className="flex gap-4">
        <div className={selectedId ? "w-1/2" : "w-full"}>
          <TraceTable
            traces={traces}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
          <Pagination
            total={total}
            limit={PAGE_SIZE}
            offset={offset}
            onChange={(o) => updateParams({ offset: String(o) })}
          />
        </div>
        {selectedId && (
          <div className="w-1/2 border-l pl-4 overflow-y-auto max-h-[calc(100vh-12rem)]">
            <TraceDetailPanel traceId={selectedId} />
          </div>
        )}
      </div>
    </div>
  );
}

export default function TracesPage() {
  return (
    <Suspense fallback={<p className="text-zinc-400 p-8">Loading...</p>}>
      <TracesContent />
    </Suspense>
  );
}
