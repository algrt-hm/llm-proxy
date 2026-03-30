import { PROVIDER_BAR_COLORS } from "@/lib/constants";

interface BarChartProps {
  data: { label: string; value: number; provider?: string }[];
  height?: number;
  formatValue?: (value: number) => string;
}

export default function BarChart({ data, height = 200, formatValue }: BarChartProps) {
  if (data.length === 0) return null;
  const max = Math.max(...data.map((d) => d.value), 1);
  const barWidth = Math.min(48, Math.floor(500 / data.length));
  const gap = 4;
  const chartWidth = data.length * (barWidth + gap);
  const labelHeight = 40;
  const valueHeight = 20;
  const barArea = height - labelHeight - valueHeight;

  return (
    <svg
      viewBox={`0 0 ${chartWidth} ${height}`}
      className="w-full"
      style={{ maxHeight: height }}
    >
      {data.map((d, i) => {
        const barH = (d.value / max) * barArea;
        const x = i * (barWidth + gap);
        const color =
          d.provider && PROVIDER_BAR_COLORS[d.provider]
            ? PROVIDER_BAR_COLORS[d.provider]
            : "#6b7280";
        return (
          <g key={i}>
            <rect
              x={x}
              y={valueHeight + barArea - barH}
              width={barWidth}
              height={barH}
              fill={color}
              rx={2}
            />
            <text
              x={x + barWidth / 2}
              y={valueHeight + barArea - barH - 4}
              textAnchor="middle"
              className="fill-zinc-600"
              fontSize={9}
            >
              {formatValue ? formatValue(d.value) : d.value.toLocaleString()}
            </text>
            <text
              x={x + barWidth / 2}
              y={height - 4}
              textAnchor="middle"
              className="fill-zinc-500"
              fontSize={8}
            >
              {d.label.length > 8 ? d.label.slice(0, 7) + "..." : d.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
