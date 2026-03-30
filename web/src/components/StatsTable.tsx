import type { ProviderModelStat } from "@/lib/types";
import { formatLatency, formatTokens, successRate } from "@/lib/utils";
import ProviderBadge from "./ProviderBadge";

interface StatsTableProps {
  stats: ProviderModelStat[];
}

export default function StatsTable({ stats }: StatsTableProps) {
  if (stats.length === 0) {
    return (
      <p className="text-zinc-400 text-sm py-8 text-center">No data</p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-zinc-500">
            <th className="py-2 pr-3 font-medium">Provider</th>
            <th className="py-2 pr-3 font-medium">Model</th>
            <th className="py-2 pr-3 font-medium text-right">Traces</th>
            <th className="py-2 pr-3 font-medium text-right">Avg Latency</th>
            <th className="py-2 pr-3 font-medium text-right">Success</th>
            <th className="py-2 pr-3 font-medium text-right">Tok In</th>
            <th className="py-2 pr-3 font-medium text-right">Tok Out</th>
            <th className="py-2 pr-3 font-medium text-right">Tok Total</th>
            <th className="py-2 pr-3 font-medium text-right">Tok Think</th>
          </tr>
        </thead>
        <tbody>
          {stats.map((s) => (
            <tr
              key={`${s.provider}-${s.model}`}
              className="border-b border-zinc-100"
            >
              <td className="py-2 pr-3">
                <ProviderBadge provider={s.provider} />
              </td>
              <td className="py-2 pr-3 font-mono text-xs">{s.model}</td>
              <td className="py-2 pr-3 text-right">{s.traces}</td>
              <td className="py-2 pr-3 text-right">
                {formatLatency(s.avg_ms)}
              </td>
              <td className="py-2 pr-3 text-right">
                {successRate(s.ok, s.traces)}
              </td>
              <td className="py-2 pr-3 text-right">{formatTokens(s.tok_in)}</td>
              <td className="py-2 pr-3 text-right">
                {formatTokens(s.tok_out)}
              </td>
              <td className="py-2 pr-3 text-right">
                {formatTokens(s.tok_total)}
              </td>
              <td className="py-2 pr-3 text-right">
                {formatTokens(s.tok_think)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
