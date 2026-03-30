"use client";

import Link from "next/link";
import type { CacheHitItem } from "@/lib/types";
import { formatDate } from "@/lib/utils";
import ProviderBadge from "./ProviderBadge";

interface CacheHitTableProps {
  items: CacheHitItem[];
  selectedId?: number | null;
  onSelect?: (item: CacheHitItem) => void;
}

export default function CacheHitTable({ items, selectedId, onSelect }: CacheHitTableProps) {
  if (items.length === 0) {
    return (
      <p className="text-zinc-400 text-sm py-8 text-center">
        No cache hits found
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-zinc-500">
            <th className="py-2 pr-3 font-medium">ID</th>
            <th className="py-2 pr-3 font-medium">Time</th>
            <th className="py-2 pr-3 font-medium">Provider</th>
            <th className="py-2 pr-3 font-medium">Model</th>
            <th className="py-2 pr-3 font-medium">Type</th>
            <th className="py-2 pr-3 font-medium">Cached Trace</th>
          </tr>
        </thead>
        <tbody>
          {items.map((h) => (
            <tr
              key={h.id}
              onClick={() => onSelect?.(h)}
              className={`border-b border-zinc-100 cursor-pointer ${
                selectedId === h.id
                  ? "bg-blue-50"
                  : "hover:bg-zinc-50"
              }`}
            >
              <td className="py-2 pr-3 font-mono">{h.id}</td>
              <td className="py-2 pr-3 text-zinc-500 whitespace-nowrap">
                {formatDate(h.created_at)}
              </td>
              <td className="py-2 pr-3">
                <ProviderBadge provider={h.provider} />
              </td>
              <td className="py-2 pr-3 font-mono text-xs max-w-48 truncate">
                {h.model}
              </td>
              <td className="py-2 pr-3">
                <span
                  className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                    h.cache_type === "response"
                      ? "bg-blue-100 text-blue-800"
                      : "bg-purple-100 text-purple-800"
                  }`}
                >
                  {h.cache_type}
                </span>
              </td>
              <td className="py-2 pr-3">
                <Link
                  href={`/traces/${h.cached_trace_id}`}
                  className="text-blue-600 hover:underline font-mono"
                >
                  {h.cached_trace_id}
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
