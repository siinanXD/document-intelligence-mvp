"use client";

import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

const KEY = "cockpit.tenantId";
const SLUG_KEY = "cockpit.tenantSlug";

export type TenantState = {
  tenantId: string;
  slug: string;
  setSlug: (slug: string) => void;
  save: (id: string, nextSlug: string) => void;
  ready: boolean;
};

const TenantContext = createContext<TenantState | null>(null);

export function TenantProvider({ children }: { children: ReactNode }) {
  const [tenantId, setTenantId] = useState("");
  const [slug, setSlug] = useState("demo");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setTenantId(window.localStorage.getItem(KEY) || "");
    setSlug(window.localStorage.getItem(SLUG_KEY) || "demo");
    setReady(true);
  }, []);

  const save = useCallback((id: string, nextSlug: string) => {
    window.localStorage.setItem(KEY, id);
    window.localStorage.setItem(SLUG_KEY, nextSlug);
    setTenantId(id);
    setSlug(nextSlug);
  }, []);

  const value = useMemo(
    () => ({ tenantId, slug, setSlug, save, ready }),
    [tenantId, slug, save, ready],
  );

  return createElement(TenantContext.Provider, { value }, children);
}

export function useTenant(): TenantState {
  const ctx = useContext(TenantContext);
  if (ctx === null) {
    throw new Error("useTenant must be used within TenantProvider");
  }
  return ctx;
}
