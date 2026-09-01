"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, createDemoTenant, failureLabel, getCockpit } from "@/lib/api";
import { useTenant } from "@/lib/useTenant";
import type { CockpitSnapshot } from "@/lib/types";

export function Shell({ children }: { children: React.ReactNode }) {
  const tenant = useTenant();
  const [cockpit, setCockpit] = useState<CockpitSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    getCockpit()
      .then(setCockpit)
      .catch((exc: unknown) => {
        if (exc instanceof ApiError) {
          setError(`${failureLabel(exc.kind)}: ${exc.detail}`);
        } else {
          setError("The API is not reachable.");
        }
      });
  }, []);

  async function onCreateTenant() {
    setCreating(true);
    setError(null);
    try {
      const created = await createDemoTenant(tenant.slug || "demo");
      tenant.save(created.id, created.slug);
    } catch (exc: unknown) {
      if (exc instanceof ApiError) {
        setError(`${failureLabel(exc.kind)}: ${exc.detail}`);
      } else {
        setError("Could not create a demo tenant.");
      }
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="shell">
      <header className="top">
        <div>
          <p className="kicker">Document intelligence</p>
          <h1>Pipeline cockpit</h1>
        </div>
        <nav>
          <Link href="/">Documents</Link>
          <Link href="/search">Search</Link>
          <Link href="/ask">Ask</Link>
          <Link href="/evaluation">Evaluation</Link>
        </nav>
      </header>
      {cockpit ? (
        <p className={cockpit.status === "ready" ? "banner ok" : "banner warn"} role="status">
          API {cockpit.status}. database {cockpit.checks.database}, vector store{" "}
          {cockpit.checks.vector_store}. embed{" "}
          {cockpit.providers.embedding_configured ? "configured" : "not configured"}, llm{" "}
          {cockpit.providers.llm_configured ? "configured" : "not configured"}.
        </p>
      ) : null}
      {error ? (
        <p className="banner error" role="alert">
          {error}
        </p>
      ) : null}
      <section className="tenant">
        <label>
          Tenant slug
          <input
            value={tenant.slug}
            onChange={(event) => tenant.setSlug(event.target.value)}
            aria-label="Tenant slug"
          />
        </label>
        <button type="button" onClick={onCreateTenant} disabled={creating || !tenant.ready}>
          {tenant.tenantId ? "Reuse demo tenant" : "Create demo tenant"}
        </button>
        <p className="muted">
          {tenant.tenantId
            ? `X-Tenant-Id ${tenant.tenantId}`
            : "Local identification only. Production auth is a later issue."}
        </p>
      </section>
      <main>{children}</main>
    </div>
  );
}
