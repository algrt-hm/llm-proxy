import { TIME_RANGES, type TimeRangeValue } from "@/lib/constants";

interface TimeRangeSelectProps {
  value: TimeRangeValue;
  onChange: (value: TimeRangeValue) => void;
}

export default function TimeRangeSelect({
  value,
  onChange,
}: TimeRangeSelectProps) {
  return (
    <div className="flex gap-1">
      {TIME_RANGES.map((range) => (
        <button
          key={range.value}
          onClick={() => onChange(range.value)}
          className={`px-3 py-1 text-xs rounded ${
            value === range.value
              ? "bg-zinc-900 text-white"
              : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200"
          }`}
        >
          {range.label}
        </button>
      ))}
    </div>
  );
}
