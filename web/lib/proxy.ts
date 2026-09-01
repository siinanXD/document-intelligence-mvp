export function publicApiBase(raw: string | undefined): string {
  if (raw === undefined || raw.trim() === "") return "http://127.0.0.1:8000";
  return raw.replace(/\/$/, "");
}

export function resolveUpstreamUrl(
  upstream: string | undefined,
  path: string[],
  search = "",
): string | null {
  const base = upstream?.replace(/\/$/, "");
  if (!base) return null;
  const suffix = path.join("/");
  return `${base}/${suffix}${search}`;
}

const HOP_BY_HOP = [
  "connection",
  "host",
  "keep-alive",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
];

export function outgoingProxyHeaders(incoming: Headers): Headers {
  const headers = new Headers(incoming);
  for (const name of HOP_BY_HOP) headers.delete(name);
  return headers;
}
