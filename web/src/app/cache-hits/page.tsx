"use client";

import { Suspense, useEffect, useState, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { fetchCacheHits, fetchProviders } from "@/lib/api";
import type { CacheHitItem } from "@/lib/types";
import CacheHitTable from "@/components/CacheHitTable";
import CacheHitDetailPanel from "@/components/CacheHitDetailPanel";
import Pagination from "@/components/Pagination";
import TimeRangeSelect from "@/components/TimeRangeSelect";
import { type TimeRangeValue, timeRangeToSince } from "@/lib/constants";

const PAGE_SIZE = 50;

function CacheHitsContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const provider = searchParams.get("provider") || "";
  const cacheType = searchParams.get("type") || "";
  const timeRange = (searchParams.get("range") || "24h") as TimeRangeValue;
  const offset = parseInt(searchParams.get("offset") || "0", 10);

  const [items, setItems] = useState<CacheHitItem[]>([]);
  const [total, setTotal] = useState(0);
  const [providers, setProviders] = useState<string[]>([]);
  const [selectedHit, setSelectedHit] = useState<CacheHitItem | null>(null);

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
      router.push(`/cache-hits?${params.toString()}`);
    },
    [router, searchParams]
  );

  useEffect(() => {
    fetchProviders().then((d) => setProviders(Object.keys(d.providers)));
  }, []);

  useEffect(() => {
    const since = timeRangeToSince(timeRange);
    setSelectedHit(null);
    fetchCacheHits({
      limit: PAGE_SIZE,
      offset,
      provider: provider || undefined,
      cache_type: cacheType || undefined,
      since,
    }).then((data) => {
      setItems(data.items);
      setTotal(data.total);
    });
  }, [provider, cacheType, timeRange, offset]);

  return (
    <div>
      <h1 className="text-xl font-semibold mb-4">Cache Hits</h1>

      <div className="flex flex-wrap items-center gap-3 mb-4">
        <select
          value={provider}
          onChange={(e) =>
            updateParams({ provider: e.target.value, offset: "0" })
          }
          className="border rounded px-2 py-1 text-sm bg-white"
        >
          <option value="">All providers</option>
          {providers.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>

        <select
          value={cacheType}
          onChange={(e) =>
            updateParams({ type: e.target.value, offset: "0" })
          }
          className="border rounded px-2 py-1 text-sm bg-white"
        >
          <option value="">All types</option>
          <option value="response">response</option>
          <option value="idempotency">idempotency</option>
        </select>

        <TimeRangeSelect
          value={timeRange}
          onChange={(v) => updateParams({ range: v, offset: "0" })}
        />
      </div>

      <div className="flex gap-4">
        <div className={selectedHit ? "w-1/2" : "w-full"}>
          <CacheHitTable
            items={items}
            selectedId={selectedHit?.id}
            onSelect={setSelectedHit}
          />
          <Pagination
            total={total}
            limit={PAGE_SIZE}
            offset={offset}
            onChange={(o) => updateParams({ offset: String(o) })}
          />
        </div>
        {selectedHit && (
          <div className="w-1/2 border-l pl-4 overflow-y-auto max-h-[calc(100vh-12rem)]">
            <CacheHitDetailPanel cacheHit={selectedHit} />
          </div>
        )}
      </div>
    </div>
  );
}

export default function CacheHitsPage() {
  return (
    <Suspense fallback={<p className="text-zinc-400 p-8">Loading...</p>}>
      <CacheHitsContent />
    </Suspense>
  );
}
