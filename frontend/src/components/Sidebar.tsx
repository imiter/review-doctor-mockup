"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useStoreContext } from "@/lib/store-context";

const NAV_ITEMS = [
  {
    href: "/dashboard",
    label: "대시보드",
    icon: (
      <svg viewBox="0 0 20 20" fill="none" className="h-4.5 w-4.5">
        <path d="M3 4a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1H4a1 1 0 01-1-1V4zM11 4a1 1 0 011-1h4a1 1 0 011 1v7a1 1 0 01-1 1h-4a1 1 0 01-1-1V4zM3 12a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1H4a1 1 0 01-1-1v-4zM11 14a1 1 0 011-1h4a1 1 0 011 1v2a1 1 0 01-1 1h-4a1 1 0 01-1-1v-2z" fill="currentColor" />
      </svg>
    ),
  },
  {
    href: "/reviews",
    label: "리뷰 관리",
    icon: (
      <svg viewBox="0 0 20 20" fill="none" className="h-4.5 w-4.5">
        <path d="M2 5a2 2 0 012-2h12a2 2 0 012 2v7a2 2 0 01-2 2H8l-4 3v-3H4a2 2 0 01-2-2V5z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
      </svg>
    ),
  },
  {
    href: "/ads",
    label: "광고 순위 모니터링",
    icon: (
      <svg viewBox="0 0 20 20" fill="none" className="h-4.5 w-4.5">
        <path d="M10 18a8 8 0 100-16 8 8 0 000 16z" stroke="currentColor" strokeWidth="1.5" />
        <path d="M10 13a3 3 0 100-6 3 3 0 000 6z" stroke="currentColor" strokeWidth="1.5" />
        <path d="M10 9.5a.5.5 0 100-1 .5.5 0 000 1z" fill="currentColor" />
      </svg>
    ),
  },
];

export function Sidebar() {
  const pathname = usePathname();
  const { user, stores, storeId, setStoreId, logout } = useStoreContext();

  return (
    <aside className="flex h-screen w-60 shrink-0 flex-col border-r border-border-subtle bg-surface">
      <div className="flex items-center gap-2 px-5 py-5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-sm font-bold text-white">D</div>
        <div>
          <p className="text-sm font-semibold leading-tight">Delivery Review</p>
          <p className="text-[11px] text-muted leading-tight">& Store Insight</p>
        </div>
      </div>

      {stores.length > 0 && (
        <div className="px-4 pb-3">
          <select
            value={storeId ?? ""}
            onChange={(e) => setStoreId(Number(e.target.value))}
            className="w-full rounded-lg border border-border-subtle bg-surface-2 px-3 py-2 text-xs outline-none focus:border-accent"
          >
            {stores.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </div>
      )}

      <nav className="flex-1 space-y-1 px-3">
        {NAV_ITEMS.map((item) => {
          const active = pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition ${
                active ? "bg-accent-soft text-accent" : "text-muted hover:bg-surface-2 hover:text-foreground"
              }`}
            >
              {item.icon}
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-border-subtle px-4 py-4">
        <p className="truncate text-xs font-medium text-foreground">{user?.nickname}</p>
        <p className="truncate text-[11px] text-muted">{user?.email}</p>
        <button
          onClick={logout}
          className="mt-3 w-full rounded-lg border border-border-subtle py-2 text-xs text-muted transition hover:border-danger hover:text-danger"
        >
          로그아웃
        </button>
      </div>
    </aside>
  );
}
