"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/", label: "Dashboard" },
  { href: "/traces", label: "Traces" },
  { href: "/cache-hits", label: "Cache Hits" },
];

export default function Nav() {
  const pathname = usePathname();

  return (
    <nav className="w-48 shrink-0 border-r border-zinc-200 bg-zinc-50 p-4 flex flex-col gap-1">
      <div className="text-sm font-bold text-zinc-500 mb-3 tracking-wide">
        llm-proxy
      </div>
      {links.map((link) => {
        const active =
          link.href === "/"
            ? pathname === "/"
            : pathname.startsWith(link.href);
        return (
          <Link
            key={link.href}
            href={link.href}
            className={`block px-3 py-2 rounded text-sm ${
              active
                ? "bg-zinc-900 text-white font-medium"
                : "text-zinc-600 hover:bg-zinc-200"
            }`}
          >
            {link.label}
          </Link>
        );
      })}
    </nav>
  );
}
