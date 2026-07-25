"use client";

import { Sidebar } from "@/components/Sidebar";
import { StoreProvider, useStoreContext } from "@/lib/store-context";

function Shell({ children }: { children: React.ReactNode }) {
  const { ready } = useStoreContext();
  if (!ready) {
    return (
      <div className="flex h-screen w-full items-center justify-center text-sm text-muted">
        불러오는 중...
      </div>
    );
  }
  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <main className="flex-1 overflow-y-auto px-8 py-8">{children}</main>
    </div>
  );
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <StoreProvider>
      <Shell>{children}</Shell>
    </StoreProvider>
  );
}
