import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Shell } from "./Shell";
import DocumentsPage from "@/app/page";
import { TenantProvider } from "@/lib/useTenant";

const getCockpit = vi.fn();
const createDemoTenant = vi.fn();
const listDocuments = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getCockpit: (...args: unknown[]) => getCockpit(...args),
    createDemoTenant: (...args: unknown[]) => createDemoTenant(...args),
    listDocuments: (...args: unknown[]) => listDocuments(...args),
  };
});

describe("shared tenant state", () => {
  beforeEach(() => {
    window.localStorage.clear();
    getCockpit.mockReset();
    createDemoTenant.mockReset();
    listDocuments.mockReset();
    getCockpit.mockResolvedValue({
      status: "ready",
      version: "0.1.0",
      environment: "local",
      checks: { database: "up", vector_store: "up" },
      providers: {
        embedding_provider: "openai",
        embedding_model: "text-embedding-3-small",
        embedding_version: "v1",
        llm_provider: "openai",
        llm_model: "gpt-4o-mini",
        embedding_configured: false,
        llm_configured: false,
      },
      evaluation: { retrieval: null, generation: null },
    });
    listDocuments.mockResolvedValue([]);
    createDemoTenant.mockResolvedValue({
      id: "11111111-1111-1111-1111-111111111111",
      slug: "demo",
      name: "Demo",
      created: true,
    });
  });

  it("lets the documents page use a tenant created in the shell without a reload", async () => {
    render(
      <TenantProvider>
        <Shell>
          <DocumentsPage />
        </Shell>
      </TenantProvider>,
    );

    expect(await screen.findByText("Create a demo tenant first.")).toBeInTheDocument();
    expect(screen.getByLabelText("Upload")).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Create demo tenant" }));

    await waitFor(() => {
      expect(screen.getByText("No documents yet.")).toBeInTheDocument();
    });
    expect(screen.getByLabelText("Upload")).toBeEnabled();
    expect(screen.getByText(/X-Tenant-Id 11111111-1111-1111-1111-111111111111/)).toBeInTheDocument();
  });
});
