"use client";

import Link from "next/link";
import { useState } from "react";
import { ApiError, askQuestion, failureLabel } from "@/lib/api";
import { DecisionPanel, askDecisionRows } from "@/components/DecisionPanel";
import { useTenant } from "@/lib/useTenant";
import type { AskResult } from "@/lib/types";

export default function AskPage() {
  const { tenantId } = useTenant();
  const [question, setQuestion] = useState("");
  const [filter, setFilter] = useState("");
  const [result, setResult] = useState<AskResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!tenantId) return;
    setError(null);
    try {
      const ids = filter
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean);
      setResult(await askQuestion(tenantId, question, ids));
    } catch (exc: unknown) {
      if (exc instanceof ApiError) setError(`${failureLabel(exc.kind)}: ${exc.detail}`);
    }
  }

  return (
    <div className="stack">
      <form className="panel stack" onSubmit={onSubmit}>
        <h2>Ask</h2>
        <label>
          Question
          <input
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            required
          />
        </label>
        <label>
          Optional document ids (comma-separated)
          <input value={filter} onChange={(event) => setFilter(event.target.value)} />
        </label>
        <button type="submit" disabled={!tenantId}>
          Ask
        </button>
        {error ? (
          <p className="error" role="alert">
            {error}
          </p>
        ) : null}
      </form>
      {result ? (
        <>
          <section className="panel">
            <h2>Answer</h2>
            {result.has_sufficient_evidence ? (
              <p>The documents support this answer.</p>
            ) : (
              <p>No sufficient evidence in the available documents.</p>
            )}
            {result.conflicting ? (
              <p className="banner warn">Cited passages disagree. Both sides are shown.</p>
            ) : null}
            <pre>{result.answer}</pre>
          </section>
          <DecisionPanel title="Answer decisions" rows={askDecisionRows(result.decisions)} />
          {result.sources.map((source) => (
            <article className="hit" key={source.source_id}>
              <p>
                <Link href={`/documents/${source.document_id}/sources/${source.source_id}`}>
                  {source.filename}
                </Link>
              </p>
              <p className="muted">
                {source.source_id}
                {source.page_number != null ? ` · page ${source.page_number}` : ""}
              </p>
              <pre>{source.text}</pre>
            </article>
          ))}
        </>
      ) : null}
    </div>
  );
}
