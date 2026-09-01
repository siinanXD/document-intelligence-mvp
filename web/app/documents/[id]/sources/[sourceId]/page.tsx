"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, failureLabel, getSource } from "@/lib/api";
import { useTenant } from "@/lib/useTenant";
import type { SourceDetail } from "@/lib/types";

export default function SourcePage() {
  const params = useParams<{ id: string; sourceId: string }>();
  const { tenantId } = useTenant();
  const [source, setSource] = useState<SourceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!tenantId) return;
    getSource(tenantId, params.id, params.sourceId)
      .then(setSource)
      .catch((exc: unknown) => {
        if (exc instanceof ApiError) setError(`${failureLabel(exc.kind)}: ${exc.detail}`);
      });
  }, [params.id, params.sourceId, tenantId]);

  if (error) {
    return (
      <p className="error" role="alert">
        {error}
      </p>
    );
  }
  if (!source) return <p className="muted">Loading source…</p>;

  return (
    <div className="stack">
      <p>
        <Link href={`/documents/${source.document_id}`}>{source.filename}</Link>
      </p>
      <section className="panel">
        <h2>Cited passage</h2>
        <p className="muted">
          {source.source_id}
          {source.page_number != null ? ` · page ${source.page_number}` : ""}
          {source.section_title ? ` · ${source.section_title}` : ""}
        </p>
        <pre>{source.text}</pre>
      </section>
    </div>
  );
}
