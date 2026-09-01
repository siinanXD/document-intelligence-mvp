import type {
  AskResult,
  CockpitSnapshot,
  DocumentRecord,
  FailureKind,
  Pipeline,
  Relation,
  SearchHit,
  SourceDetail,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") || "http://127.0.0.1:8000";

export class ApiError extends Error {
  status: number;
  kind: FailureKind;
  detail: string;

  constructor(status: number, detail: string, kind: FailureKind) {
    super(detail);
    this.status = status;
    this.kind = kind;
    this.detail = detail;
  }
}

export function classifyHttpError(
  status: number,
  detail: string,
  checks?: Record<string, string>,
): FailureKind {
  const text = detail.toLowerCase();
  if (status === 400 && text.includes("tenant")) return "configuration";
  if (text.includes("not configured")) return "configuration";
  if (checks?.database === "down") return "database";
  if (checks?.vector_store === "down") return "vector_store";
  if (text.includes("vector") || text.includes("search is temporarily")) return "vector_store";
  if (text.includes("answering") || text.includes("provider")) return "provider";
  if (status === 415 || status === 413) return "document_processing";
  if (status === 503) return "provider";
  return "unknown";
}

function readDetail(payload: unknown): string {
  if (typeof payload === "object" && payload !== null && "detail" in payload) {
    const detail = (payload as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return "Request failed";
}

async function apiFetch(
  path: string,
  init: RequestInit & { tenant?: string | null } = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.tenant) headers.set("X-Tenant-Id", init.tenant);
  if (init.body && !(init.body instanceof FormData) && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = readDetail(await response.json());
    } catch {
      /* keep status text */
    }
    throw new ApiError(response.status, detail, classifyHttpError(response.status, detail));
  }
  return response;
}

export async function getCockpit(): Promise<CockpitSnapshot> {
  const response = await apiFetch("/meta/cockpit");
  return response.json();
}

export async function createDemoTenant(slug = "demo", name = "Demo"): Promise<{
  id: string;
  slug: string;
  name: string;
  created: boolean;
}> {
  const response = await apiFetch("/dev/tenants", {
    method: "POST",
    body: JSON.stringify({ slug, name }),
  });
  return response.json();
}

export async function listDocuments(tenant: string): Promise<DocumentRecord[]> {
  const response = await apiFetch("/documents", { tenant });
  return response.json();
}

export async function getDocument(tenant: string, id: string): Promise<DocumentRecord> {
  const response = await apiFetch(`/documents/${id}`, { tenant });
  return response.json();
}

export async function getPipeline(tenant: string, id: string): Promise<Pipeline> {
  const response = await apiFetch(`/documents/${id}/pipeline`, { tenant });
  return response.json();
}

export async function getRelations(tenant: string, id: string): Promise<Relation[]> {
  const response = await apiFetch(`/documents/${id}/relations`, { tenant });
  return response.json();
}

export async function getSource(
  tenant: string,
  documentId: string,
  sourceId: string,
): Promise<SourceDetail> {
  const response = await apiFetch(`/documents/${documentId}/sources/${sourceId}`, { tenant });
  return response.json();
}

export async function uploadDocument(
  tenant: string,
  file: File,
): Promise<{ document: DocumentRecord; duplicate: boolean }> {
  const body = new FormData();
  body.append("file", file);
  const response = await apiFetch("/documents", { method: "POST", tenant, body });
  return response.json();
}

export async function searchDocuments(
  tenant: string,
  query: string,
  mode: "semantic" | "lexical",
  documentIds?: string[],
): Promise<{ mode: string; results: SearchHit[] }> {
  const response = await apiFetch("/search", {
    method: "POST",
    tenant,
    body: JSON.stringify({
      query,
      mode,
      document_ids: documentIds && documentIds.length > 0 ? documentIds : undefined,
    }),
  });
  return response.json();
}

export async function askQuestion(
  tenant: string,
  question: string,
  documentIds?: string[],
): Promise<AskResult> {
  const response = await apiFetch("/ask", {
    method: "POST",
    tenant,
    body: JSON.stringify({
      question,
      document_ids: documentIds && documentIds.length > 0 ? documentIds : undefined,
    }),
  });
  return response.json();
}

export function failureLabel(kind: FailureKind | string | null | undefined): string {
  switch (kind) {
    case "configuration":
      return "Configuration";
    case "provider":
      return "AI provider";
    case "database":
      return "Database";
    case "vector_store":
      return "Vector store";
    case "document_processing":
      return "Document processing";
    default:
      return "Unknown";
  }
}
