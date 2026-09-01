"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ApiError, failureLabel, listDocuments, uploadDocument } from "@/lib/api";
import { useTenant } from "@/lib/useTenant";
import type { DocumentRecord } from "@/lib/types";

export default function DocumentsPage() {
  const { tenantId } = useTenant();
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!tenantId) return;
    try {
      setDocuments(await listDocuments(tenantId));
    } catch (exc: unknown) {
      if (exc instanceof ApiError) setError(`${failureLabel(exc.kind)}: ${exc.detail}`);
    }
  }, [tenantId]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  async function onUpload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file || !tenantId) return;
    setError(null);
    try {
      const result = await uploadDocument(tenantId, file);
      setNotice(
        result.duplicate
          ? "These exact bytes are already stored for this tenant. Nothing was re-queued."
          : `${result.document.filename} queued.`,
      );
      await refresh();
    } catch (exc: unknown) {
      if (exc instanceof ApiError) setError(`${failureLabel(exc.kind)}: ${exc.detail}`);
    }
  }

  return (
    <div className="stack">
      <section className="panel">
        <h2>Documents</h2>
        <p className="muted">
          Upload a PDF, DOCX, PPTX, XLSX, HTML, Markdown or text file. The worker moves it
          through uploaded → parsed → chunked → embedded → indexed → ready.
        </p>
        <label>
          Upload
          <input type="file" onChange={onUpload} disabled={!tenantId} />
        </label>
        {notice ? <p role="status">{notice}</p> : null}
        {error ? (
          <p className="error" role="alert">
            {error}
          </p>
        ) : null}
      </section>
      {!tenantId ? <p className="muted">Create a demo tenant first.</p> : null}
      {tenantId && documents.length === 0 ? (
        <p className="muted">No documents yet.</p>
      ) : (
        <ul className="list">
          {documents.map((document) => (
            <li key={document.id}>
              <Link href={`/documents/${document.id}`}>{document.filename}</Link>
              <p className="muted">
                {document.status}
                {document.document_type ? ` · ${document.document_type}` : ""}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
