"use client";

import { useEffect, useState } from "react";

const KEY = "cockpit.tenantId";
const SLUG_KEY = "cockpit.tenantSlug";

export function useTenant() {
  const [tenantId, setTenantId] = useState<string>("");
  const [slug, setSlug] = useState<string>("demo");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setTenantId(window.localStorage.getItem(KEY) || "");
    setSlug(window.localStorage.getItem(SLUG_KEY) || "demo");
    setReady(true);
  }, []);

  function save(id: string, nextSlug: string) {
    window.localStorage.setItem(KEY, id);
    window.localStorage.setItem(SLUG_KEY, nextSlug);
    setTenantId(id);
    setSlug(nextSlug);
  }

  return { tenantId, slug, setSlug, save, ready };
}
