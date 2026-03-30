import { NextRequest, NextResponse } from "next/server";
import pool from "@/lib/db";

const ALLOWED_SORT_COLS = new Set([
  "id",
  "created_at",
  "provider",
  "model",
  "status_code",
  "latency_ms",
]);

export async function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl;
  const limit = Number(searchParams.get("limit") ?? 50);
  const offset = Number(searchParams.get("offset") ?? 0);
  const provider = searchParams.get("provider");
  const model = searchParams.get("model");
  const status_filter = searchParams.get("status_filter");
  const since = searchParams.get("since");
  const until = searchParams.get("until");
  const sort = searchParams.get("sort") ?? "id";
  const order = searchParams.get("order") ?? "desc";

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
  if (status_filter === "ok") {
    conditions.push("status_code >= 200");
    conditions.push("status_code < 300");
  } else if (status_filter === "error") {
    conditions.push("(status_code IS NULL OR status_code >= 400)");
  }
  if (searchParams.get("exclude_embeddings") === "1") {
    conditions.push("model NOT LIKE '%embedding%'");
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

  const sortCol = ALLOWED_SORT_COLS.has(sort) ? sort : "id";
  const sortDir = order === "asc" ? "ASC" : "DESC";

  const countQuery = `SELECT COUNT(*) FROM traces ${where}`;
  const dataQuery = `
    SELECT id, request_id, created_at, provider, model,
           status_code, latency_ms, error,
           request_json IS NOT NULL AS has_request,
           response_json IS NOT NULL AS has_response
    FROM traces
    ${where}
    ORDER BY ${sortCol} ${sortDir}
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
      request_id: r.request_id,
      created_at: r.created_at ? r.created_at.toISOString() : null,
      provider: r.provider,
      model: r.model,
      status_code: r.status_code,
      latency_ms: r.latency_ms,
      error: r.error,
      has_request: r.has_request,
      has_response: r.has_response,
    })),
    total: Number(countResult.rows[0].count),
    limit,
    offset,
  });
}
