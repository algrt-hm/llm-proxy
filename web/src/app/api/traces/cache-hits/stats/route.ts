import { NextRequest, NextResponse } from "next/server";
import pool from "@/lib/db";

export async function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl;
  const since = searchParams.get("since");
  const until = searchParams.get("until");

  const traceConditions: string[] = [
    "t.status_code >= 200",
    "t.status_code < 300",
  ];
  const cacheConditions: string[] = [];
  const params: string[] = [];
  let idx = 1;

  if (since) {
    traceConditions.push(`t.created_at >= $${idx}`);
    cacheConditions.push(`created_at >= $${idx}`);
    params.push(since);
    idx++;
  }
  if (until) {
    traceConditions.push(`t.created_at <= $${idx}`);
    cacheConditions.push(`created_at <= $${idx}`);
    params.push(until);
    idx++;
  }

  const traceWhere = traceConditions.join(" AND ");
  const cacheWhere =
    cacheConditions.length > 0
      ? `WHERE ${cacheConditions.join(" AND ")}`
      : "";

  const query = `
    SELECT t.provider, t.model,
    COUNT(DISTINCT t.id) AS traces,
    COALESCE(c.cache_hits, 0) AS cache_hits,
    ROUND(COALESCE(c.cache_hits, 0) * 100.0
        / NULLIF(COUNT(DISTINCT t.id) + COALESCE(c.cache_hits, 0), 0), 1) AS hit_pct
    FROM traces t
    LEFT JOIN (
        SELECT provider, model, COUNT(*) AS cache_hits
        FROM cache_hits
        ${cacheWhere}
        GROUP BY provider, model
    ) c ON t.provider = c.provider AND t.model = c.model
    WHERE ${traceWhere}
    GROUP BY t.provider, t.model, c.cache_hits
    ORDER BY traces DESC
  `;

  const { rows } = await pool.query(query, params);

  return NextResponse.json({
    items: rows.map((r) => ({
      provider: r.provider,
      model: r.model,
      traces: Number(r.traces),
      cache_hits: Number(r.cache_hits),
      hit_pct: r.hit_pct ? Number(r.hit_pct) : 0,
    })),
  });
}
