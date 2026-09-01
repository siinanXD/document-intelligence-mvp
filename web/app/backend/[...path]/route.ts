import { type NextRequest } from "next/server";
import { outgoingProxyHeaders, resolveUpstreamUrl } from "@/lib/proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type RouteContext = { params: Promise<{ path: string[] }> };

async function proxy(request: NextRequest, context: RouteContext): Promise<Response> {
  const { path } = await context.params;
  const target = resolveUpstreamUrl(
    process.env.API_UPSTREAM_URL,
    path,
    request.nextUrl.search,
  );
  if (target === null) {
    return new Response("API upstream is not configured", { status: 502 });
  }

  const headers = outgoingProxyHeaders(request.headers);
  const init: RequestInit & { duplex?: "half" } = {
    method: request.method,
    headers,
    redirect: "manual",
  };
  if (request.method !== "GET" && request.method !== "HEAD" && request.body) {
    init.body = request.body;
    init.duplex = "half";
  }

  const upstream = await fetch(target, init);
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: outgoingProxyHeaders(upstream.headers),
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const HEAD = proxy;
export const OPTIONS = proxy;
