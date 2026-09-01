"use client";

import Link from "next/link";
import { useState } from "react";
import { ApiError, failureLabel, searchDocuments } from "@/lib/api";
import { DecisionPanel } from "@/components/DecisionPanel";
import { useTenant } from "@/lib/useTenant";
import type { SearchHit } from "@/lib/types";

export default function SearchPage() {
  const { tenantId } = useTenant();
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"semantic" | "lexical">("semantic");
  const [filter, setFilter] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [usedMode, setUsedMode] = useState<string | null>(null);
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
      const result = await searchDocuments(tenantId, query, mode, ids);
      setHits(result.results);
      setUsedMode(result.mode);
    } catch (exc: unknown) {
      if (exc instanceof ApiError) setError(`${failureLabel(exc.kind)}: ${exc.detail}`);
    }
  }

  return (
    <div className="stack">
      <form className="panel stack" onSubmit={onSubmit}>
        <h2>Search</h2>
        <label>
          Query
          <input value={query} onChange={(event) => setQuery(event.target.value)} required />
        </label>
        <label>
          Mode
          <select
            value={mode}
            onChange={(event) => setMode(event.target.value as "semantic" | "lexical")}
          >
            <option value="semantic">semantic</option>
            <option value="lexical">lexical</option>
          </select>
        </label>
        <label>
          Optional document ids (comma-separated)
          <input value={filter} onChange={(event) => setFilter(event.target.value)} />
        </label>
        <button type="submit" disabled={!tenantId}>
          Search
        </button>
        {error ? (
          <p className="error" role="alert">
            {error}
          </p>
        ) : null}
      </form>
      {usedMode ? (
        <DecisionPanel
          title="Search decisions"
          rows={[
            { label: "Mode", value: usedMode },
            { label: "Hits", value: String(hits.length) },
            { label: "Document filter", value: filter || "none" },
          ]}
        />
      ) : null}
      {hits.length === 0 && usedMode ? <p className="muted">No matching passages.</p> : null}
      {hits.map((hit) => (
        <article className="hit" key={hit.source_id}>
          <p>
            <Link href={`/documents/${hit.document_id}/sources/${hit.source_id}`}>
              {hit.filename}
            </Link>{" "}
            · score {hit.score.toFixed(3)}
          </p>
          <p className="muted">
            {hit.source_id}
            {hit.page_number != null ? ` · page ${hit.page_number}` : ""}
            {hit.section_title ? ` · ${hit.section_title}` : ""}
          </p>
          <pre>{hit.text}</pre>
        </article>
      ))}
    </div>
  );
}
