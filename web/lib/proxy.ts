export function backendRewrites(
  upstream: string | undefined,
): { source: string; destination: string }[] {
  const base = upstream?.replace(/\/$/, "");
  if (!base) return [];
  return [{ source: "/backend/:path*", destination: `${base}/:path*` }];
}

export function publicApiBase(raw: string | undefined): string {
  if (raw === undefined) return "http://127.0.0.1:8000";
  return raw.replace(/\/$/, "");
}
