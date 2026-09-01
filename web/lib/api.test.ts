import { describe, expect, it } from "vitest";
import { classifyHttpError, failureLabel } from "./api";
import { backendRewrites, publicApiBase } from "./proxy";

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
  it("defaults to the local API and keeps an empty production prefix", () => {
    expect(publicApiBase(undefined)).toBe("http://127.0.0.1:8000");
    expect(publicApiBase("")).toBe("");
    expect(publicApiBase("/backend/")).toBe("/backend");
  });
});

describe("backendRewrites", () => {
  it("proxies /backend to a private upstream without exposing that host in the client", () => {
    expect(backendRewrites(undefined)).toEqual([]);
    expect(backendRewrites("http://api.railway.internal:8000/")).toEqual([
      {
        source: "/backend/:path*",
        destination: "http://api.railway.internal:8000/:path*",
      },
    ]);
  });
});
