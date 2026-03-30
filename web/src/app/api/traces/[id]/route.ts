import { NextRequest, NextResponse } from "next/server";
import pool from "@/lib/db";

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const traceId = Number(id);

  const { rows } = await pool.query("SELECT * FROM traces WHERE id = $1", [
    traceId,
  ]);

  if (rows.length === 0) {
    return NextResponse.json(
      { error: `Trace ${traceId} not found` },
      { status: 404 }
    );
  }

  const trace = rows[0];

  let request_data = null;
  if (trace.request_json) {
    try {
      request_data =
        typeof trace.request_json === "string"
          ? JSON.parse(trace.request_json)
          : trace.request_json;
    } catch {
      request_data = trace.request_json;
    }
  }

  let response_data = null;
  if (trace.response_json) {
    try {
      response_data =
        typeof trace.response_json === "string"
          ? JSON.parse(trace.response_json)
          : trace.response_json;
    } catch {
      response_data = trace.response_json;
    }
  }

  return NextResponse.json({
    id: trace.id,
    request_id: trace.request_id,
    created_at: trace.created_at ? trace.created_at.toISOString() : null,
    provider: trace.provider,
    model: trace.model,
    status_code: trace.status_code,
    latency_ms: trace.latency_ms,
    error: trace.error,
    idempotency_key: trace.idempotency_key,
    cache_key: trace.cache_key,
    request_json: request_data,
    response_json: response_data,
  });
}
