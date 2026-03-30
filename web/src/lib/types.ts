export interface TraceListItem {
  id: number;
  request_id: string;
  created_at: string | null;
  provider: string;
  model: string;
  status_code: number | null;
  latency_ms: number | null;
  error: string | null;
  has_request: boolean;
  has_response: boolean;
}

export interface TraceDetail extends TraceListItem {
  idempotency_key: string | null;
  cache_key: string | null;
  request_json: unknown;
  response_json: unknown;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ProviderModels {
  providers: Record<string, string[]>;
}

export interface ProviderModelStat {
  provider: string;
  model: string;
  traces: number;
  avg_ms: number;
  ok: number;
  errors: number;
  tok_in: number;
  tok_out: number;
  tok_total: number;
  tok_think: number;
}

export interface StatsTotals {
  traces: number;
  avg_ms: number;
  ok: number;
  errors: number;
  tok_in: number;
  tok_out: number;
  tok_total: number;
  tok_think: number;
}

export interface StatsResponse {
  by_provider_model: ProviderModelStat[];
  totals: StatsTotals;
}

export interface CacheHitItem {
  id: number;
  created_at: string | null;
  provider: string;
  model: string;
  cache_type: string;
  cached_trace_id: number;
}

export interface CacheHitStat {
  provider: string;
  model: string;
  traces: number;
  cache_hits: number;
  hit_pct: number;
}

export interface CacheHitStatsResponse {
  items: CacheHitStat[];
}
