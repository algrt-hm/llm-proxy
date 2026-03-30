import type {
  CacheHitItem,
  CacheHitStatsResponse,
  PaginatedResponse,
  ProviderModels,
  StatsResponse,
  TraceDetail,
  TraceListItem,
} from "./types";

const BASE = "/api/traces";

function qs(params: Record<string, string | number | undefined>): string {
  const entries = Object.entries(params).filter(
    ([, v]) => v !== undefined && v !== ""
  );
  if (entries.length === 0) return "";
  return "?" + new URLSearchParams(entries.map(([k, v]) => [k, String(v)])).toString();
}

export async function fetchProviders(): Promise<ProviderModels> {
  const res = await fetch(`${BASE}/providers`);
  if (!res.ok) return { providers: {} };
  return res.json();
}

export async function fetchStats(params: {
  since?: string;
  until?: string;
}): Promise<StatsResponse | null> {
  const res = await fetch(`${BASE}/stats${qs(params)}`);
  if (!res.ok) return null;
  return res.json();
}

export async function fetchTraces(params: {
  limit?: number;
  offset?: number;
  provider?: string;
  model?: string;
  status_filter?: string;
  since?: string;
  until?: string;
  exclude_embeddings?: string;
  sort?: string;
  order?: string;
}): Promise<PaginatedResponse<TraceListItem>> {
  const res = await fetch(`${BASE}${qs(params)}`);
  if (!res.ok) return { items: [], total: 0, limit: 0, offset: 0 };
  return res.json();
}

export async function fetchTrace(id: number): Promise<TraceDetail> {
  const res = await fetch(`${BASE}/${id}`);
  if (!res.ok) throw new Error(`Trace ${id} not found`);
  return res.json();
}

export async function fetchCacheHits(params: {
  limit?: number;
  offset?: number;
  provider?: string;
  model?: string;
  cache_type?: string;
  since?: string;
  until?: string;
}): Promise<PaginatedResponse<CacheHitItem>> {
  const res = await fetch(`${BASE}/cache-hits${qs(params)}`);
  if (!res.ok) return { items: [], total: 0, limit: 0, offset: 0 };
  return res.json();
}

export async function fetchCacheHitStats(params: {
  since?: string;
  until?: string;
}): Promise<CacheHitStatsResponse | null> {
  const res = await fetch(`${BASE}/cache-hits/stats${qs(params)}`);
  if (!res.ok) return null;
  return res.json();
}
