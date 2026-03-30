import { NextRequest, NextResponse } from "next/server";
import pool from "@/lib/db";

export async function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl;
  const since = searchParams.get("since");
  const until = searchParams.get("until");

  const conditions: string[] = [];
  const params: string[] = [];
  let idx = 1;

  if (since) {
    conditions.push(`created_at >= $${idx++}`);
    params.push(since);
  }
  if (until) {
    conditions.push(`created_at <= $${idx++}`);
    params.push(until);
  }

  const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";

  // Strip JSON \u0000 escape sequences before casting to jsonb (Postgres rejects them)
  const jsonb = `replace(response_json, '\\u0000', '')::jsonb`;

  const query = `
    SELECT provider, model, COUNT(*) AS traces,
    ROUND(AVG(latency_ms)::numeric, 0) AS avg_ms,
    SUM(CASE WHEN status_code >= 200 AND status_code < 300 THEN 1 ELSE 0 END) AS ok,
    SUM(CASE WHEN status_code IS NULL OR status_code >= 400 THEN 1 ELSE 0 END) AS errors,
    COALESCE(SUM((${jsonb} #>> '{usage,prompt_tokens}')::bigint), 0) AS tok_in,
    COALESCE(SUM((${jsonb} #>> '{usage,completion_tokens}')::bigint), 0) AS tok_out,
    COALESCE(SUM((${jsonb} #>> '{usage,total_tokens}')::bigint), 0) AS tok_total,
    COALESCE(SUM((${jsonb} #>> '{usage,total_tokens}')::bigint), 0)
    - COALESCE(SUM((${jsonb} #>> '{usage,prompt_tokens}')::bigint), 0)
    - COALESCE(SUM((${jsonb} #>> '{usage,completion_tokens}')::bigint), 0) AS tok_think
    FROM traces
    ${where}
    GROUP BY provider, model
    ORDER BY traces DESC
  `;

  const totalsQuery = `
    SELECT COUNT(*) AS traces,
    ROUND(AVG(latency_ms)::numeric, 0) AS avg_ms,
    SUM(CASE WHEN status_code >= 200 AND status_code < 300 THEN 1 ELSE 0 END) AS ok,
    SUM(CASE WHEN status_code IS NULL OR status_code >= 400 THEN 1 ELSE 0 END) AS errors,
    COALESCE(SUM((${jsonb} #>> '{usage,prompt_tokens}')::bigint), 0) AS tok_in,
    COALESCE(SUM((${jsonb} #>> '{usage,completion_tokens}')::bigint), 0) AS tok_out,
    COALESCE(SUM((${jsonb} #>> '{usage,total_tokens}')::bigint), 0) AS tok_total,
    COALESCE(SUM((${jsonb} #>> '{usage,total_tokens}')::bigint), 0)
    - COALESCE(SUM((${jsonb} #>> '{usage,prompt_tokens}')::bigint), 0)
    - COALESCE(SUM((${jsonb} #>> '{usage,completion_tokens}')::bigint), 0) AS tok_think
    FROM traces
    ${where}
  `;

  const [result, totalsResult] = await Promise.all([
    pool.query(query, params),
    pool.query(totalsQuery, params),
  ]);

  const by_provider_model = result.rows.map((r) => ({
    provider: r.provider,
    model: r.model,
    traces: Number(r.traces),
    avg_ms: r.avg_ms ? Number(r.avg_ms) : 0,
    ok: Number(r.ok),
    errors: Number(r.errors),
    tok_in: Number(r.tok_in),
    tok_out: Number(r.tok_out),
    tok_total: Number(r.tok_total),
    tok_think: Number(r.tok_think),
  }));

  const t = totalsResult.rows[0];
  const totals = t
    ? {
        traces: Number(t.traces),
        avg_ms: t.avg_ms ? Number(t.avg_ms) : 0,
        ok: Number(t.ok),
        errors: Number(t.errors),
        tok_in: Number(t.tok_in),
        tok_out: Number(t.tok_out),
        tok_total: Number(t.tok_total),
        tok_think: Number(t.tok_think),
      }
    : {};

  return NextResponse.json({ by_provider_model, totals });
}
