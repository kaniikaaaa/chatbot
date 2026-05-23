"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Chat" },
  { href: "/dashboard", label: "Dashboard" },
  { href: "/logs", label: "Logs" },
];

export function SideNav() {
  const pathname = usePathname();

  return (
    <div className="border-b bg-white p-3">
      <div className="mb-2 text-sm font-semibold">Inference Logger</div>
      <nav className="flex gap-2 text-sm">
        {LINKS.map((item) => {
          const active = pathname === item.href;
          return (
            <Link
              key={item.href}
              className={`rounded-md px-3 py-1.5 ${active ? "bg-gray-900 text-white" : "hover:bg-gray-100"}`}
              href={item.href}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
