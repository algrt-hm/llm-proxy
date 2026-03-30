"use client";

import type { TraceListItem } from "@/lib/types";
import { formatDate, formatLatency } from "@/lib/utils";
import StatusBadge from "./StatusBadge";
import ProviderBadge from "./ProviderBadge";

interface TraceTableProps {
  traces: TraceListItem[];
  selectedId?: number | null;
  onSelect?: (id: number) => void;
}

export default function TraceTable({ traces, selectedId, onSelect }: TraceTableProps) {
  if (traces.length === 0) {
    return (
      <p className="text-zinc-400 text-sm py-8 text-center">No traces found</p>
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
            <th className="py-2 pr-3 font-medium">Status</th>
            <th className="py-2 pr-3 font-medium text-right">Latency</th>
          </tr>
        </thead>
        <tbody>
          {traces.map((t) => (
            <tr
              key={t.id}
              onClick={() => onSelect?.(t.id)}
              className={`border-b border-zinc-100 cursor-pointer ${
                selectedId === t.id
                  ? "bg-blue-50"
                  : "hover:bg-zinc-50"
              }`}
            >
              <td className="py-2 pr-3 font-mono text-blue-600">
                {t.id}
              </td>
              <td className="py-2 pr-3 text-zinc-500 whitespace-nowrap">
                {formatDate(t.created_at)}
              </td>
              <td className="py-2 pr-3">
                <ProviderBadge provider={t.provider} />
              </td>
              <td className="py-2 pr-3 font-mono text-xs max-w-48 truncate">
                {t.model}
              </td>
              <td className="py-2 pr-3">
                <StatusBadge status={t.status_code} />
              </td>
              <td className="py-2 pr-3 text-right text-zinc-500 whitespace-nowrap">
                {formatLatency(t.latency_ms)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
