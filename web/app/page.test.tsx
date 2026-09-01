import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/useTenant", () => ({
  useTenant: () => ({
    tenantId: "11111111-1111-1111-1111-111111111111",
    slug: "demo",
    setSlug: vi.fn(),
    save: vi.fn(),
    ready: true,
  }),
}));

const listDocuments = vi.fn();
const uploadDocument = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listDocuments: (...args: unknown[]) => listDocuments(...args),
    uploadDocument: (...args: unknown[]) => uploadDocument(...args),
  };
});

import DocumentsPage from "@/app/page";

describe("documents happy path", () => {
  beforeEach(() => {
    listDocuments.mockReset();
    uploadDocument.mockReset();
    listDocuments.mockResolvedValue([]);
  });

  it("uploads a file and lists the queued document", async () => {
    uploadDocument.mockResolvedValue({
      duplicate: false,
      document: {
        id: "22222222-2222-2222-2222-222222222222",
        filename: "manual.pdf",
        mime_type: "application/pdf",
        file_hash: "a".repeat(64),
        status: "queued",
        title: null,
        document_type: null,
        parser_name: null,
        embedding_provider: null,
        embedding_model: null,
        embedding_version: null,
        created_at: "2026-09-01T00:00:00Z",
        updated_at: "2026-09-01T00:00:00Z",
      },
    });
    listDocuments
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([
        {
          id: "22222222-2222-2222-2222-222222222222",
          filename: "manual.pdf",
          mime_type: "application/pdf",
          file_hash: "a".repeat(64),
          status: "queued",
          title: null,
          document_type: null,
          parser_name: null,
          embedding_provider: null,
          embedding_model: null,
          embedding_version: null,
          created_at: "2026-09-01T00:00:00Z",
          updated_at: "2026-09-01T00:00:00Z",
        },
      ]);

    render(<DocumentsPage />);
    const file = new File(["%PDF-1.7"], "manual.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("Upload"), { target: { files: [file] } });

    await waitFor(() => {
      expect(screen.getByText("manual.pdf queued.")).toBeInTheDocument();
      expect(screen.getByText("manual.pdf")).toBeInTheDocument();
    });
    expect(uploadDocument).toHaveBeenCalled();
  });
});
