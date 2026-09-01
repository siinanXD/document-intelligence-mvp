import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PipelineTimeline } from "./PipelineTimeline";
import type { Pipeline } from "@/lib/types";

const pipeline: Pipeline = {
  stages: [
    { id: "uploaded", complete: true, current: false },
    { id: "parsed", complete: true, current: false },
    { id: "chunked", complete: false, current: true },
    { id: "embedded", complete: false, current: false },
    { id: "indexed", complete: false, current: false },
    { id: "ready", complete: false, current: false },
  ],
  current_stage: "chunked",
  failed: false,
  failure_class: null,
  error_type: null,
  chunk_count: 0,
  parser_name: "stub",
  embedding_provider: null,
  embedding_model: null,
  embedding_version: null,
  job_status: "processing",
  job_attempts: 1,
};

describe("PipelineTimeline", () => {
  it("shows the current stage in the sequence", () => {
    render(<PipelineTimeline pipeline={pipeline} />);
    expect(screen.getByText("Chunked")).toBeInTheDocument();
    expect(screen.getByText("current")).toBeInTheDocument();
    expect(screen.getByText("Uploaded")).toBeInTheDocument();
  });
});
