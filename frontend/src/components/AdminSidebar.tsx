"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Logo } from "@/components/Logo";
import { clearToken } from "@/lib/api";

const NAV = [
  { href: "/ops-4k9x2m/payments", label: "결제 이력" },
  { href: "/ops-4k9x2m/stores", label: "매장 운영 현황" },
  { href: "/ops-4k9x2m/users", label: "유저 관리" },
];

export function AdminSidebar() {
  const pathname = usePathname();
  const router = useRouter();

  return (
    <aside className="flex h-screen w-64 shrink-0 flex-col overflow-y-auto border-r border-border-subtle bg-surface">
      <div className="flex items-center gap-2.5 px-5 py-5">
        <Logo size={32} />
        <div>
          <p className="text-sm font-semibold leading-tight">스토어 타겟</p>
          <p className="text-[11px] text-muted leading-tight">관리자</p>
        </div>
      </div>

      <nav className="flex-1 space-y-1 px-3 pb-4">
        {NAV.map((item) => {
          const active = pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`block rounded-lg px-3 py-2.5 text-sm transition ${
                active ? "bg-accent-soft text-accent" : "text-muted hover:bg-surface-2 hover:text-foreground"
              }`}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-border-subtle px-4 py-4 space-y-2">
        <Link
          href="/dashboard"
          className="block text-center text-xs text-muted hover:text-foreground"
        >
          사장님 화면으로 돌아가기
        </Link>
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
  );
}
