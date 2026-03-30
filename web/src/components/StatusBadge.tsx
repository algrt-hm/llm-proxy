export default function StatusBadge({
  status,
}: {
  status: number | null;
}) {
  if (status === null) {
    return (
      <span className="inline-block px-2 py-0.5 rounded text-xs font-medium bg-zinc-100 text-zinc-500">
        —
      </span>
    );
  }
  const color =
    status >= 200 && status < 300
      ? "bg-green-100 text-green-800"
      : status >= 400 && status < 500
        ? "bg-amber-100 text-amber-800"
        : "bg-red-100 text-red-800";
  return (
    <span
      className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${color}`}
    >
      {status}
    </span>
  );
}
