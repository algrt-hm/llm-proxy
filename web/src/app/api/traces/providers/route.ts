import { NextResponse } from "next/server";
import pool from "@/lib/db";

export async function GET() {
  const { rows } = await pool.query(
    "SELECT DISTINCT provider, model FROM traces ORDER BY provider, model"
  );

  const providers: Record<string, string[]> = {};
  for (const row of rows) {
    const list = providers[row.provider] ?? [];
    list.push(row.model);
    providers[row.provider] = list;
  }

  return NextResponse.json({ providers });
}
