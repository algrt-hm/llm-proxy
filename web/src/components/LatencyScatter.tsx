"use client";

import { useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  LATENCY_DOT_COLOR,
  LATENCY_ERROR_COLOR,
  PERCENTILE_LINE_COLORS,
} from "@/lib/constants";
import type { LatencySeries } from "@/lib/types";
import { formatDate, formatLatency } from "@/lib/utils";

export type LatencyScale = "log" | "linear";

// Fewer requests than this and the percentile lines are left out: five lines
// interpolated between a handful of dots say nothing.
export const MIN_POINTS_FOR_PERCENTILES = 10;

interface LatencyScatterProps {
  series: LatencySeries;
  percentiles: number[];
  // Shared by every panel on the page, epoch ms.
  xDomain: [number, number];
  scale: LatencyScale;
}

// SVG user units; the chart scales to its container's width.
const W = 560;
const H = 250;
const LEFT = 44;
const RIGHT = 70;
const TOP = 10;
const BOTTOM = 24;
const PLOT_W = W - LEFT - RIGHT;
const PLOT_H = H - TOP - BOTTOM;
const DOT_R = 2.2;
const CROSS = 2.6;
const LABEL_GAP = 11;
// How close the pointer must be to a dot to pick it up.
const HIT_RADIUS = 20;

function isError(status: number | null): boolean {
  return status === null || status >= 400;
}

function percentileLabel(p: number): string {
  return `p${Math.round(p * 100)}`;
}

function formatTick(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = ms / 1000;
  return `${s >= 10 || Number.isInteger(s) ? Math.round(s) : s.toFixed(1)}s`;
}

function formatTime(ms: number, spanMs: number): string {
  const d = new Date(ms);
  if (spanMs <= 48 * 3600_000) {
    return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

function linearTicks(max: number): number[] {
  const rough = max / 4;
  const pow = Math.pow(10, Math.floor(Math.log10(rough)));
  const step = [1, 2, 5, 10].map((m) => m * pow).find((s) => s >= rough) ?? rough;
  const ticks: number[] = [];
  for (let v = 0; v <= max + step / 2; v += step) ticks.push(v);
  return ticks;
}

export default function LatencyScatter({
  series,
  percentiles,
  xDomain,
  scale,
}: LatencyScatterProps) {
  const router = useRouter();
  const svgRef = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);

  const geometry = useMemo(() => {
    const latencies = series.points.map((p) => p[2]);
    const max = Math.max(...latencies, 1);
    const min = Math.max(Math.min(...latencies), 1);

    let yTicks: number[];
    let y: (ms: number) => number;
    if (scale === "log") {
      const lo = Math.floor(Math.log10(min));
      const hi = Math.max(Math.ceil(Math.log10(max)), lo + 1);
      yTicks = [];
      for (let e = lo; e <= hi; e++) yTicks.push(Math.pow(10, e));
      y = (ms) => TOP + PLOT_H * (1 - (Math.log10(Math.max(ms, 1)) - lo) / (hi - lo));
    } else {
      yTicks = linearTicks(max);
      const top = yTicks[yTicks.length - 1];
      y = (ms) => TOP + PLOT_H * (1 - ms / top);
    }

    const [x0, x1] = xDomain;
    const x = (ts: number) => LEFT + (PLOT_W * (ts - x0)) / Math.max(x1 - x0, 1);

    // All dots as one path and all error crosses as another: thousands of
    // separate elements per panel would make the page sluggish.
    const xs: number[] = [];
    const ys: number[] = [];
    let dots = "";
    let crosses = "";
    for (const [, ts, ms, status] of series.points) {
      const px = Math.round(x(ts) * 10) / 10;
      const py = Math.round(y(ms) * 10) / 10;
      xs.push(px);
      ys.push(py);
      if (isError(status)) {
        crosses += `M${px - CROSS} ${py - CROSS}l${2 * CROSS} ${2 * CROSS}M${px - CROSS} ${py + CROSS}l${2 * CROSS} ${-2 * CROSS}`;
      } else {
        dots += `M${px - DOT_R} ${py}a${DOT_R} ${DOT_R} 0 1 0 ${2 * DOT_R} 0a${DOT_R} ${DOT_R} 0 1 0 ${-2 * DOT_R} 0`;
      }
    }

    // Percentile lines, with their labels pushed apart where they would overlap.
    const lines =
      series.n >= MIN_POINTS_FOR_PERCENTILES
        ? series.percentiles.map((ms, i) => ({
            label: `${percentileLabel(percentiles[i])} ${formatLatency(ms)}`,
            color: PERCENTILE_LINE_COLORS[i] ?? PERCENTILE_LINE_COLORS[PERCENTILE_LINE_COLORS.length - 1],
            y: y(ms),
            labelY: y(ms),
          }))
        : [];
    const byHeight = [...lines].sort((a, b) => a.y - b.y);
    for (let i = 1; i < byHeight.length; i++) {
      byHeight[i].labelY = Math.max(byHeight[i].labelY, byHeight[i - 1].labelY + LABEL_GAP);
    }
    const floor = TOP + PLOT_H;
    for (let i = byHeight.length - 1; i >= 0; i--) {
      const limit = i === byHeight.length - 1 ? floor : byHeight[i + 1].labelY - LABEL_GAP;
      byHeight[i].labelY = Math.min(byHeight[i].labelY, limit);
    }

    const span = x1 - x0;
    const xTicks = [0, 1, 2, 3, 4].map((i) => x0 + (span * i) / 4);

    return { xs, ys, dots, crosses, lines, yTicks, y, x, xTicks, span };
  }, [series, percentiles, xDomain, scale]);

  function nearest(event: React.MouseEvent<SVGSVGElement>): number | null {
    const svg = svgRef.current;
    if (!svg) return null;
    const rect = svg.getBoundingClientRect();
    const px = ((event.clientX - rect.left) * W) / rect.width;
    const py = ((event.clientY - rect.top) * H) / rect.height;
    let best: number | null = null;
    let bestDistance = HIT_RADIUS * HIT_RADIUS;
    for (let i = 0; i < geometry.xs.length; i++) {
      const dx = geometry.xs[i] - px;
      const dy = geometry.ys[i] - py;
      const distance = dx * dx + dy * dy;
      if (distance < bestDistance) {
        bestDistance = distance;
        best = i;
      }
    }
    return best;
  }

  const hovered = hover !== null ? series.points[hover] : null;
  const hoverX = hover !== null ? geometry.xs[hover] : 0;
  const hoverY = hover !== null ? geometry.ys[hover] : 0;

  return (
    <div className="relative">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className={`w-full ${hovered ? "cursor-pointer" : ""}`}
        role="img"
        aria-label={`Latency over time for ${series.provider} ${series.model}, ${series.n} requests`}
        onMouseMove={(event) => setHover(nearest(event))}
        onMouseLeave={() => setHover(null)}
        onClick={() => hovered && router.push(`/traces/${hovered[0]}`)}
      >
        {geometry.yTicks.map((tick) => (
          <g key={tick}>
            <line x1={LEFT} x2={LEFT + PLOT_W} y1={geometry.y(tick)} y2={geometry.y(tick)} stroke="#e4e4e7" strokeWidth={1} />
            <text x={LEFT - 6} y={geometry.y(tick) + 3} textAnchor="end" fontSize={10} className="fill-zinc-500">
              {formatTick(tick)}
            </text>
          </g>
        ))}
        <line x1={LEFT} x2={LEFT + PLOT_W} y1={TOP + PLOT_H} y2={TOP + PLOT_H} stroke="#a1a1aa" strokeWidth={1} />
        {geometry.xTicks.map((tick, i) => (
          <text
            key={tick}
            x={geometry.x(tick)}
            y={H - 7}
            textAnchor={i === 0 ? "start" : i === geometry.xTicks.length - 1 ? "end" : "middle"}
            fontSize={10}
            className="fill-zinc-500"
          >
            {formatTime(tick, geometry.span)}
          </text>
        ))}

        <path d={geometry.dots} fill={LATENCY_DOT_COLOR} fillOpacity={0.5} />
        <path d={geometry.crosses} stroke={LATENCY_ERROR_COLOR} strokeWidth={1.4} fill="none" />

        {geometry.lines.map((line) => (
          <g key={line.label}>
            <line x1={LEFT} x2={LEFT + PLOT_W} y1={line.y} y2={line.y} stroke={line.color} strokeWidth={1.5} />
            {/* The label may be nudged away from its line, so a swatch ties the two together */}
            <circle cx={LEFT + PLOT_W + 7} cy={line.labelY} r={2.5} fill={line.color} />
            <text x={LEFT + PLOT_W + 13} y={line.labelY + 3} fontSize={10} className="fill-zinc-700">
              {line.label}
            </text>
          </g>
        ))}

        {hovered && (
          <circle cx={hoverX} cy={hoverY} r={5} fill="none" stroke="#18181b" strokeWidth={1.5} />
        )}
      </svg>

      {hovered && (
        <div
          className="absolute z-10 pointer-events-none rounded border border-zinc-200 bg-white px-2 py-1 text-xs shadow-sm whitespace-nowrap"
          style={{
            left: `${(hoverX / W) * 100}%`,
            top: `${(hoverY / H) * 100}%`,
            transform: `translate(${hoverX > W / 2 ? "calc(-100% - 10px)" : "10px"}, -50%)`,
          }}
        >
          <div className="font-medium text-zinc-900">{formatLatency(hovered[2])}</div>
          <div className="text-zinc-500">{formatDate(new Date(hovered[1]).toISOString())}</div>
          <div className="text-zinc-500">
            {hovered[3] === null ? "no response" : `HTTP ${hovered[3]}`} · trace {hovered[0]}
          </div>
        </div>
      )}
    </div>
  );
}
