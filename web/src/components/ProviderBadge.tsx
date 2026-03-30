import { PROVIDER_COLORS } from "@/lib/constants";

export default function ProviderBadge({ provider }: { provider: string }) {
  const color = PROVIDER_COLORS[provider] || "bg-zinc-100 text-zinc-800";
  return (
    <span
      className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${color}`}
    >
      {provider}
    </span>
  );
}
