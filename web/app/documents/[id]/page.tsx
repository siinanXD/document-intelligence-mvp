"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError, failureLabel, getDocument, getPipeline, getRelations } from "@/lib/api";
import { DecisionPanel, pipelineDecisionRows } from "@/components/DecisionPanel";
import { PipelineTimeline } from "@/components/PipelineTimeline";
import { useTenant } from "@/lib/useTenant";
import type { DocumentRecord, Pipeline, Relation } from "@/lib/types";

export default function DocumentDetailPage() {
  const params = useParams<{ id: string }>();
  const { tenantId } = useTenant();
  const [document, setDocument] = useState<DocumentRecord | null>(null);
  const [pipeline, setPipeline] = useState<Pipeline | null>(null);
  const [relations, setRelations] = useState<Relation[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!tenantId || !params.id) return;
    try {
      const [nextDocument, nextPipeline, nextRelations] = await Promise.all([
        getDocument(tenantId, params.id),
        getPipeline(tenantId, params.id),
        getRelations(tenantId, params.id),
      ]);
      setDocument(nextDocument);
      setPipeline(nextPipeline);
      setRelations(nextRelations);
      setError(null);
    } catch (exc: unknown) {
      if (exc instanceof ApiError) setError(`${failureLabel(exc.kind)}: ${exc.detail}`);
    }
  }, [params.id, tenantId]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  if (error) {
    return (
      <p className="error" role="alert">
        {error}
      </p>
    );
  }
  if (!document || !pipeline) {
    return <p className="muted">Loading pipeline…</p>;
  }

  return (
    <div className="stack">
      <p>
        <Link href="/">Documents</Link>
      </p>
      <section className="panel">
        <h2>{document.filename}</h2>
        <p className="muted">
          {document.status}
          {document.title ? ` · ${document.title}` : ""}
        </p>
      </section>
      <PipelineTimeline pipeline={pipeline} />
      <DecisionPanel title="Ingestion decisions" rows={pipelineDecisionRows(pipeline)} />
      <section className="panel">
        <h2>Relations</h2>
        {relations.length === 0 ? (
          <p className="muted">No relations recorded yet.</p>
        ) : (
          <ul className="list">
            {relations.map((relation) => (
              <li key={`${relation.relation_type}-${relation.target.document_id}`}>
                <Link href={`/documents/${relation.target.document_id}`}>
                  {relation.target.filename}
                </Link>
                <p className="muted">
                  {relation.relation_type}
                  {relation.score == null ? "" : ` · ${relation.score}`}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
