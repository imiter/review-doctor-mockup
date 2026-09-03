"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { useStoreContext } from "@/lib/store-context";
import { Logo } from "@/components/Logo";

const ICONS = {
  dashboard: (
    <svg viewBox="0 0 20 20" fill="none" className="h-4.5 w-4.5">
      <path d="M3 4a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1H4a1 1 0 01-1-1V4zM11 4a1 1 0 011-1h4a1 1 0 011 1v7a1 1 0 01-1 1h-4a1 1 0 01-1-1V4zM3 12a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1H4a1 1 0 01-1-1v-4zM11 14a1 1 0 011-1h4a1 1 0 011 1v2a1 1 0 01-1 1h-4a1 1 0 01-1-1v-2z" fill="currentColor" />
    </svg>
  ),
  ads: (
    <svg viewBox="0 0 20 20" fill="none" className="h-4.5 w-4.5">
      <path d="M10 18a8 8 0 100-16 8 8 0 000 16z" stroke="currentColor" strokeWidth="1.5" />
      <path d="M10 13a3 3 0 100-6 3 3 0 000 6z" stroke="currentColor" strokeWidth="1.5" />
      <path d="M10 9.5a.5.5 0 100-1 .5.5 0 000 1z" fill="currentColor" />
    </svg>
  ),
  reviews: (
    <svg viewBox="0 0 20 20" fill="none" className="h-4.5 w-4.5">
      <path d="M2 5a2 2 0 012-2h12a2 2 0 012 2v7a2 2 0 01-2 2H8l-4 3v-3H4a2 2 0 01-2-2V5z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  ),
  rules: (
    <svg viewBox="0 0 20 20" fill="none" className="h-4.5 w-4.5">
      <circle cx="10" cy="10" r="7" stroke="currentColor" strokeWidth="1.5" />
      <path d="M10 6v4l3 2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  ),
  style: (
    <svg viewBox="0 0 20 20" fill="none" className="h-4.5 w-4.5">
      <path d="M4 15l2.5-.5L15 6a1.5 1.5 0 00-2-2l-8.5 8.5L4 15z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  ),
  sales: (
    <svg viewBox="0 0 20 20" fill="none" className="h-4.5 w-4.5">
      <path d="M3 16V8m5 8V4m5 12v-6m5 6V9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  ),
  orders: (
    <svg viewBox="0 0 20 20" fill="none" className="h-4.5 w-4.5">
      <rect x="3" y="4" width="14" height="13" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
      <path d="M6 8h8M6 11h8M6 14h5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  ),
  store: (
    <svg viewBox="0 0 20 20" fill="none" className="h-4.5 w-4.5">
      <path d="M3 8l1-4h12l1 4M3 8a2 2 0 004 0 2 2 0 004 0 2 2 0 004 0 2 2 0 004 0M4 8v7a1 1 0 001 1h10a1 1 0 001-1V8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  account: (
    <svg viewBox="0 0 20 20" fill="none" className="h-4.5 w-4.5">
      <circle cx="10" cy="7" r="3" stroke="currentColor" strokeWidth="1.5" />
      <path d="M4 17c0-3 3-5 6-5s6 2 6 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  ),
  billing: (
    <svg viewBox="0 0 20 20" fill="none" className="h-4.5 w-4.5">
      <rect x="2.5" y="5" width="15" height="10" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
      <path d="M2.5 8.5h15" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  ),
};

type NavItem = { href: string; label: string; icon: keyof typeof ICONS };
type NavSection = { header?: string; items: NavItem[] };

const NAV: NavSection[] = [
  { items: [{ href: "/dashboard", label: "대시보드", icon: "dashboard" }] },
  { items: [{ href: "/ads", label: "광고 순위 모니터링", icon: "ads" }] },
  {
    header: "리뷰 & 답글",
    items: [
      { href: "/reviews", label: "리뷰 관리", icon: "reviews" },
      { href: "/reviews/rules", label: "답글 규칙 설정", icon: "rules" },
      { href: "/reviews/styles", label: "답글 스타일 설정", icon: "style" },
    ],
  },
  {
    header: "매출",
    items: [
      { href: "/sales", label: "매출", icon: "sales" },
      { href: "/sales/orders", label: "주문내역", icon: "orders" },
    ],
  },
  {
    header: "내 정보 관리",
    items: [
      { href: "/account/stores", label: "가게 연결", icon: "store" },
      { href: "/account/profile", label: "계정 관리", icon: "account" },
      { href: "/account/billing", label: "구독 관리", icon: "billing" },
    ],
  },
];

// 모바일에서는 사이드바를 기본 숨기고, 상단 고정 바의 햄버거 버튼으로 여닫는
// 슬라이드 드로어로 바꾼다 (가로 스크롤 없이 화면에 맞춰 보이게 하는 게 목적).
// 데스크톱(md 이상)에서는 기존처럼 항상 펼쳐진 고정 폭 사이드바로 되돌아간다.
export function Sidebar() {
  const pathname = usePathname();
  const { user, logout } = useStoreContext();
  const [open, setOpen] = useState(false);

  return (
    <>
      <header className="fixed inset-x-0 top-0 z-30 flex h-14 items-center justify-between border-b border-border-subtle bg-surface px-4 md:hidden">
        <div className="flex items-center gap-2">
          <Logo size={26} />
          <p className="text-sm font-semibold leading-tight">스토어 타겟</p>
        </div>
        <button
          onClick={() => setOpen(true)}
          aria-label="메뉴 열기"
          className="-mr-2 rounded-lg p-2 text-muted transition hover:bg-surface-2 hover:text-foreground"
        >
          <svg viewBox="0 0 20 20" fill="none" className="h-5 w-5">
            <path d="M3 5h14M3 10h14M3 15h14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
        </button>
      </header>

      {open && (
        <div
          className="fixed inset-0 z-40 bg-black/60 md:hidden"
          onClick={() => setOpen(false)}
          aria-hidden="true"
        />
      )}

      <aside
        className={`fixed inset-y-0 left-0 z-50 flex h-screen w-64 shrink-0 flex-col overflow-y-auto border-r border-border-subtle bg-surface transition-transform duration-200 ease-out md:static md:z-auto md:translate-x-0 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between gap-2.5 px-5 py-5">
          <div className="flex items-center gap-2.5">
            <Logo size={32} />
            <div>
              <p className="text-sm font-semibold leading-tight">스토어 타겟</p>
              <p className="text-[11px] text-muted leading-tight">Store Target</p>
            </div>
          </div>
          <button
            onClick={() => setOpen(false)}
            aria-label="메뉴 닫기"
            className="rounded-lg p-1.5 text-muted transition hover:bg-surface-2 hover:text-foreground md:hidden"
          >
            ✕
          </button>
        </div>

        <nav className="flex-1 space-y-4 px-3 pb-4">
          {NAV.map((section, i) => (
            <div key={i}>
              {section.header && (
                <p className="mb-1 px-3 text-[11px] font-semibold uppercase tracking-wide text-muted">
                  {section.header}
                </p>
              )}
              <div className="space-y-1">
                {section.items.map((item) => {
                  const active = pathname === item.href;
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      onClick={() => setOpen(false)}
                      className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition ${
                        active ? "bg-accent-soft text-accent" : "text-muted hover:bg-surface-2 hover:text-foreground"
                      }`}
                    >
                      {ICONS[item.icon]}
                      {item.label}
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        <div className="border-t border-border-subtle px-4 py-4">
          <p className="truncate text-xs font-medium text-foreground">{user?.nickname}</p>
          <p className="truncate text-[11px] text-muted">{user?.email ?? "카카오 계정"}</p>
          <button
            onClick={logout}
            className="mt-3 w-full rounded-lg border border-border-subtle py-2 text-xs text-muted transition hover:border-danger hover:text-danger"
          >
            로그아웃
          </button>
        </div>
      </aside>
    </>
  );
}
