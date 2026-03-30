import { Pool } from "pg";

if (!process.env.LLM_PROXY_DATABASE_URL) {
  throw new Error("LLM_PROXY_DATABASE_URL is not set");
}

const pool = new Pool({ connectionString: process.env.LLM_PROXY_DATABASE_URL });

export default pool;
