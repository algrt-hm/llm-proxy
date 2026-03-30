interface PaginationProps {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
}

export default function Pagination({
  total,
  limit,
  offset,
  onChange,
}: PaginationProps) {
  const start = total === 0 ? 0 : offset + 1;
  const end = Math.min(offset + limit, total);
  const hasPrev = offset > 0;
  const hasNext = offset + limit < total;

  return (
    <div className="flex items-center justify-between text-sm text-zinc-500 mt-4">
      <span>
        Showing {start}–{end} of {total}
      </span>
      <div className="flex gap-2">
        <button
          disabled={!hasPrev}
          onClick={() => onChange(Math.max(0, offset - limit))}
          className="px-3 py-1 border rounded disabled:opacity-30 hover:bg-zinc-100 disabled:hover:bg-transparent"
        >
          Prev
        </button>
        <button
          disabled={!hasNext}
          onClick={() => onChange(offset + limit)}
          className="px-3 py-1 border rounded disabled:opacity-30 hover:bg-zinc-100 disabled:hover:bg-transparent"
        >
          Next
        </button>
      </div>
    </div>
  );
}
