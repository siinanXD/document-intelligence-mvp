import { describe, expect, it } from "vitest";
import { classifyHttpError, failureLabel } from "./api";

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
