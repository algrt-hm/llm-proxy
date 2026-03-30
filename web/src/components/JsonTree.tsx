"use client";

import { useState } from "react";

const ALWAYS_EXPAND = new Set(["usage", "choices", "error", "data"]);
const MAX_PREVIEW = 200;

function ValueDisplay({ value }: { value: unknown }) {
  if (value === null) return <span className="text-zinc-400">null</span>;
  if (typeof value === "boolean")
    return <span className="text-amber-600">{String(value)}</span>;
  if (typeof value === "number")
    return <span className="text-blue-600">{value}</span>;
  if (typeof value === "string") {
    if (value.length > MAX_PREVIEW) {
      return <StringPreview value={value} />;
    }
    return <span className="text-green-700">&quot;{value}&quot;</span>;
  }
  return <span className="text-zinc-500">{String(value)}</span>;
}

function StringPreview({ value }: { value: string }) {
  const [expanded, setExpanded] = useState(false);
  if (expanded) {
    return (
      <span className="text-green-700">
        &quot;{value}&quot;{" "}
        <button
          onClick={() => setExpanded(false)}
          className="text-xs text-blue-500 hover:underline"
        >
          show less
        </button>
      </span>
    );
  }
  return (
    <span className="text-green-700">
      &quot;{value.slice(0, MAX_PREVIEW)}...&quot;{" "}
      <button
        onClick={() => setExpanded(true)}
        className="text-xs text-blue-500 hover:underline"
      >
        show more
      </button>
    </span>
  );
}

function CollapsedPreview({ value }: { value: unknown }) {
  if (Array.isArray(value)) {
    return <span className="text-zinc-400">[{value.length} items]</span>;
  }
  if (typeof value === "object" && value !== null) {
    const keys = Object.keys(value);
    return <span className="text-zinc-400">{`{${keys.length} keys}`}</span>;
  }
  return null;
}

interface JsonNodeProps {
  keyName?: string;
  value: unknown;
  depth: number;
  defaultExpanded?: boolean;
  expandAll?: boolean;
}

function JsonNode({ keyName, value, depth, defaultExpanded, expandAll }: JsonNodeProps) {
  const isExpandable =
    value !== null &&
    typeof value === "object" &&
    (Array.isArray(value) ? value.length > 0 : Object.keys(value).length > 0);

  const shouldAutoExpand = expandAll
    ? true
    : defaultExpanded !== undefined
      ? defaultExpanded
      : depth < 2 || (keyName !== undefined && ALWAYS_EXPAND.has(keyName));

  const [expanded, setExpanded] = useState(shouldAutoExpand);

  if (!isExpandable) {
    return (
      <div className="flex gap-1" style={{ paddingLeft: depth * 16 }}>
        {keyName !== undefined && (
          <span className="text-zinc-500">{keyName}: </span>
        )}
        <ValueDisplay value={value} />
      </div>
    );
  }

  const entries = Array.isArray(value)
    ? value.map((v, i) => [String(i), v] as const)
    : Object.entries(value as Record<string, unknown>);

  return (
    <div>
      <div
        className="flex items-center gap-1 cursor-pointer hover:bg-zinc-50"
        style={{ paddingLeft: depth * 16 }}
        onClick={() => setExpanded(!expanded)}
      >
        <span className="text-xs text-zinc-400 w-4 text-center select-none">
          {expanded ? "\u25BC" : "\u25B6"}
        </span>
        {keyName !== undefined && (
          <span className="text-zinc-500">{keyName}: </span>
        )}
        {!expanded && <CollapsedPreview value={value} />}
        {expanded && (
          <span className="text-zinc-400">
            {Array.isArray(value) ? "[" : "{"}
          </span>
        )}
      </div>
      {expanded && (
        <>
          {entries.map(([k, v]) => (
            <JsonNode
              key={k}
              keyName={Array.isArray(value) ? undefined : k}
              value={v}
              depth={depth + 1}
              expandAll={expandAll}
            />
          ))}
          <div style={{ paddingLeft: depth * 16 }} className="text-zinc-400">
            <span className="w-4 inline-block" />
            {Array.isArray(value) ? "]" : "}"}
          </div>
        </>
      )}
    </div>
  );
}

export default function JsonTree({ data, expandAll }: { data: unknown; expandAll?: boolean }) {
  if (data === null || data === undefined) {
    return <span className="text-zinc-400 text-sm">No data</span>;
  }
  return (
    <div className="font-mono text-xs leading-5 overflow-x-auto">
      <JsonNode value={data} depth={0} defaultExpanded={true} expandAll={expandAll} />
    </div>
  );
}
