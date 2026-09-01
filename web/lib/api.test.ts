import { describe, expect, it } from "vitest";
import { classifyHttpError, failureLabel } from "./api";
import { outgoingProxyHeaders, publicApiBase, resolveUpstreamUrl } from "./proxy";

describe("classifyHttpError", () => {
  it("maps configuration and vector-store failures from the API message", () => {
    expect(classifyHttpError(503, "answering is not configured")).toBe("configuration");
    expect(classifyHttpError(503, "search is temporarily unavailable")).toBe("vector_store");
    expect(classifyHttpError(400, "X-Tenant-Id header is required")).toBe("configuration");
    expect(classifyHttpError(503, "down", { database: "down", vector_store: "up" })).toBe(
      "database",
    );
  });
});

describe("failureLabel", () => {
  it("uses plain language", () => {
    expect(failureLabel("document_processing")).toBe("Document processing");
  });
});

describe("publicApiBase", () => {
  it("defaults to the local API, including an empty .env.local value", () => {
    expect(publicApiBase(undefined)).toBe("http://127.0.0.1:8000");
    expect(publicApiBase("")).toBe("http://127.0.0.1:8000");
    expect(publicApiBase("   ")).toBe("http://127.0.0.1:8000");
    expect(publicApiBase("/backend/")).toBe("/backend");
  });
});

describe("resolveUpstreamUrl", () => {
  it("builds a private upstream URL at runtime without exposing it to the client", () => {
    expect(resolveUpstreamUrl(undefined, ["health"])).toBeNull();
    expect(
      resolveUpstreamUrl("http://api.railway.internal:8000/", ["documents", "abc"], "?limit=1"),
    ).toBe("http://api.railway.internal:8000/documents/abc?limit=1");
  });
});

describe("outgoingProxyHeaders", () => {
  it("forwards tenant identification and drops hop-by-hop headers", () => {
    const headers = outgoingProxyHeaders(
      new Headers({
        host: "web.up.railway.app",
        "x-tenant-id": "11111111-1111-1111-1111-111111111111",
        connection: "keep-alive",
      }),
    );
    expect(headers.get("x-tenant-id")).toBe("11111111-1111-1111-1111-111111111111");
    expect(headers.get("host")).toBeNull();
    expect(headers.get("connection")).toBeNull();
  });
});
