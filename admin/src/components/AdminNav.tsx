"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { Logo } from "@/components/Logo";
import { clearToken } from "@/lib/api";

const NAV = [
  { href: "/payments", label: "결제 이력" },
  { href: "/stores", label: "매장 운영 현황" },
  { href: "/users", label: "유저 관리" },
  { href: "/llmops", label: "LLMOps" },
];

export function AdminNav() {
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);

  return (
    <>
      <header className="fixed inset-x-0 top-0 z-30 flex h-14 items-center justify-between border-b border-border-subtle bg-surface px-4 md:hidden">
        <div className="flex items-center gap-2">
          <Logo size={26} />
          <p className="text-sm font-semibold leading-tight">관리자</p>
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
              <p className="text-[11px] text-muted leading-tight">관리자</p>
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

        <nav className="flex-1 space-y-1 px-3 pb-4">
          {NAV.map((item) => {
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => setOpen(false)}
                className={`block rounded-lg px-3 py-2.5 text-sm transition ${
                  active ? "bg-accent-soft text-accent" : "text-muted hover:bg-surface-2 hover:text-foreground"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="border-t border-border-subtle px-4 py-4">
          <button
            onClick={() => {
              clearToken();
              router.replace("/login");
            }}
            className="w-full rounded-lg border border-border-subtle py-2 text-xs text-muted transition hover:border-danger hover:text-danger"
          >
            로그아웃
          </button>
        </div>
      </aside>
    </>
  );
}
