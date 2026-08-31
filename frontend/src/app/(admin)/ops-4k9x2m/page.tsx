"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function AdminHome() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/ops-4k9x2m/payments");
  }, [router]);

  return null;
}
