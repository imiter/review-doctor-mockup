"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AdminNav } from "@/components/AdminNav";
import { getToken } from "@/lib/api";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    setReady(true);
  }, [router]);

  if (!ready) {
    return (
      <div className="flex h-screen w-full items-center justify-center text-sm text-muted">
        불러오는 중...
      </div>
    );
  }

  return (
    <div className="flex min-h-screen">
      <AdminNav />
      <main className="flex-1 overflow-y-auto px-4 pb-8 pt-20 md:px-8 md:py-8">{children}</main>
    </div>
  );
}
