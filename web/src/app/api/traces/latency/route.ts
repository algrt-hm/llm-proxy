import { NextRequest, NextResponse } from "next/server";
import { PROVIDER_COLORS } from "@/lib/constants";
import pool from "@/lib/db";

// Percentiles drawn on the latency page, in this order.
const PERCENTILES = [0.5, 0.75, 0.9, 0.95, 0.99];

// Providers whose trace "model" is already one series: a model name, or for
// Brave an endpoint. Anything else is a REST upstream, whose "model" is the
// whole request path with its arguments in it (Journey/JourneyResults/A/to/B),
// so those are grouped by the first path segment (Journey, StopPoint).
const WHOLE_MODEL_PROVIDERS = [...Object.keys(PROVIDER_COLORS), "brave"];

export async function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl;
  const since = searchParams.get("since");
  const until = searchParams.get("until");

  const conditions: string[] = ["latency_ms IS NOT NULL"];
  const params: (string | string[])[] = [WHOLE_MODEL_PROVIDERS];
  let idx = 2;

  if (since) {
    conditions.push(`created_at >= $${idx++}`);
    params.push(since);
  }
  if (until) {
    conditions.push(`created_at <= $${idx++}`);
    params.push(until);
  }

  const seriesKey = "CASE WHEN provider = ANY($1) THEN model ELSE split_part(model, '/', 1) END";

  // One row per provider/model (or provider/endpoint): its percentiles over
  // every request in the window, and each request as [id, epoch ms, latency ms, status code].
  const query = `
    SELECT provider, ${seriesKey} AS model, COUNT(*) AS n,
    AVG(latency_ms) AS mean, STDDEV_POP(latency_ms) AS stddev,
    percentile_cont(ARRAY[${PERCENTILES.join(", ")}]) WITHIN GROUP (ORDER BY latency_ms) AS percentiles,
    json_agg(json_build_array(
      id, (EXTRACT(EPOCH FROM created_at) * 1000)::bigint, latency_ms, status_code
    ) ORDER BY created_at) AS points
    FROM traces
    WHERE ${conditions.join(" AND ")}
    GROUP BY provider, 2
    ORDER BY n DESC, provider, 2
  `;

  const { rows } = await pool.query(query, params);

  const series = rows.map((r) => ({
    provider: r.provider,
    model: r.model,
    n: Number(r.n),
    mean: Number(r.mean),
    stddev: Number(r.stddev),
    percentiles: (r.percentiles as number[]).map(Number),
    points: r.points,
  }));

  return NextResponse.json({ percentiles: PERCENTILES, series });
}
