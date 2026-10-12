// One entry per LLM provider. The latency route also reads these names to tell
// an LLM provider from a REST upstream, so a new provider must be added here.
export const PROVIDER_COLORS: Record<string, string> = {
  openai: "bg-green-100 text-green-800",
  openrouter: "bg-purple-100 text-purple-800",
  perplexity: "bg-blue-100 text-blue-800",
  gemini: "bg-amber-100 text-amber-800",
  cerebras: "bg-red-100 text-red-800",
  anthropic: "bg-orange-100 text-orange-800",
  deepseek: "bg-cyan-100 text-cyan-800",
};

export const PROVIDER_BAR_COLORS: Record<string, string> = {
  openai: "#22c55e",
  openrouter: "#a855f7",
  perplexity: "#3b82f6",
  gemini: "#f59e0b",
  cerebras: "#ef4444",
  anthropic: "#f97316",
  deepseek: "#06b6d4",
};

// Latency page. Percentile lines are one hue, light to dark with the
// percentile, in the order the API returns them (p50, p75, p90, p95, p99).
export const PERCENTILE_LINE_COLORS = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#0d366b"];
export const LATENCY_DOT_COLOR = "#71717a";
export const LATENCY_ERROR_COLOR = "#d03b3b";

export const TIME_RANGES = [
  { label: "1h", value: "1h", hours: 1 },
  { label: "6h", value: "6h", hours: 6 },
  { label: "24h", value: "24h", hours: 24 },
  { label: "7d", value: "7d", hours: 168 },
  { label: "30d", value: "30d", hours: 720 },
  { label: "All", value: "all", hours: 0 },
] as const;

export type TimeRangeValue = (typeof TIME_RANGES)[number]["value"];

export function timeRangeToSince(value: TimeRangeValue): string | undefined {
  const range = TIME_RANGES.find((r) => r.value === value);
  if (!range || range.hours === 0) return undefined;
  const since = new Date(Date.now() - range.hours * 3600_000);
  return since.toISOString();
}
