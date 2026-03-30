import { NextRequest, NextResponse } from "next/server";
import pool from "@/lib/db";

export async function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl;
  const limit = Number(searchParams.get("limit") ?? 50);
  const offset = Number(searchParams.get("offset") ?? 0);
  const provider = searchParams.get("provider");
  const model = searchParams.get("model");
  const cache_type = searchParams.get("cache_type");
  const since = searchParams.get("since");
  const until = searchParams.get("until");

  const conditions: string[] = [];
  const params: (string | number)[] = [];
  let idx = 1;

  if (provider) {
    conditions.push(`provider = $${idx++}`);
    params.push(provider);
  }
  if (model) {
    conditions.push(`model = $${idx++}`);
    params.push(model);
  }
  if (cache_type) {
    conditions.push(`cache_type = $${idx++}`);
    params.push(cache_type);
  }
  if (since) {
    conditions.push(`created_at >= $${idx++}`);
    params.push(since);
  }
  if (until) {
    conditions.push(`created_at <= $${idx++}`);
    params.push(until);
  }

  const where =
    conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";

  const countQuery = `SELECT COUNT(*) FROM cache_hits ${where}`;
  const dataQuery = `
    SELECT id, created_at, provider, model, cache_type, cached_trace_id
    FROM cache_hits
    ${where}
    ORDER BY id DESC
    LIMIT $${idx++} OFFSET $${idx++}
  `;

  const dataParams = [...params, limit, offset];

  const [countResult, dataResult] = await Promise.all([
    pool.query(countQuery, params),
    pool.query(dataQuery, dataParams),
  ]);

  return NextResponse.json({
    items: dataResult.rows.map((r) => ({
      id: r.id,
      created_at: r.created_at ? r.created_at.toISOString() : null,
      provider: r.provider,
      model: r.model,
      cache_type: r.cache_type,
      cached_trace_id: r.cached_trace_id,
    })),
    total: Number(countResult.rows[0].count),
    limit,
    offset,
  });
}
