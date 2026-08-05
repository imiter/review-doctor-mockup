"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiGet, clearToken, getToken } from "@/lib/api";

type MeResponse = { id: number; email: string | null; nickname: string; has_phone: boolean; marketing_agreed: boolean };
type StoreOption = { id: number; name: string; category: string };

type StoreContextValue = {
  user: MeResponse | null;
  stores: StoreOption[];
  storeId: number | null;
  setStoreId: (id: number) => void;
  ready: boolean;
  logout: () => void;
  refreshUser: () => Promise<void>;
};

const StoreContext = createContext<StoreContextValue | null>(null);

export function StoreProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<MeResponse | null>(null);
  const [stores, setStores] = useState<StoreOption[]>([]);
  const [storeId, setStoreIdState] = useState<number | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    Promise.all([apiGet<MeResponse>("/auth/me"), apiGet<StoreOption[]>("/stores")])
      .then(([me, storeList]) => {
        setUser(me);
        setStores(storeList);
        const saved = Number(window.localStorage.getItem("dris_store_id"));
        const initial = storeList.find((s) => s.id === saved)?.id ?? storeList[0]?.id ?? null;
        setStoreIdState(initial);
        setReady(true);
      })
      .catch(() => router.replace("/login"));
  }, [router]);

  const setStoreId = useCallback((id: number) => {
    setStoreIdState(id);
    window.localStorage.setItem("dris_store_id", String(id));
  }, []);

  const logout = useCallback(() => {
    clearToken();
    router.replace("/login");
  }, [router]);

  const refreshUser = useCallback(async () => {
    setUser(await apiGet<MeResponse>("/auth/me"));
  }, []);

  return (
    <StoreContext.Provider value={{ user, stores, storeId, setStoreId, ready, logout, refreshUser }}>
      {children}
    </StoreContext.Provider>
  );
}

export function useStoreContext() {
  const ctx = useContext(StoreContext);
  if (!ctx) throw new Error("useStoreContext must be used within StoreProvider");
  return ctx;
}
