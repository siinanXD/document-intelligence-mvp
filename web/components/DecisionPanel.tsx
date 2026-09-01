import type { AskDecisions, Pipeline } from "@/lib/types";

type Row = { label: string; value: string };

export function DecisionPanel({
  title,
  rows,
}: {
  title: string;
  rows: Row[];
}) {
  return (
    <section className="panel">
      <h2>{title}</h2>
      <dl className="decisions">
        {rows.map((row) => (
          <div key={row.label}>
            <dt>{row.label}</dt>
            <dd>{row.value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export function pipelineDecisionRows(pipeline: Pipeline): Row[] {
  return [
    { label: "Current stage", value: pipeline.current_stage },
    { label: "Chunks", value: String(pipeline.chunk_count) },
    { label: "Parser", value: pipeline.parser_name || "not yet" },
    {
      label: "Embedding",
      value: pipeline.embedding_provider
        ? `${pipeline.embedding_provider}/${pipeline.embedding_model}@${pipeline.embedding_version}`
        : "not yet",
    },
    { label: "Job", value: pipeline.job_status || "none" },
    {
      label: "Attempts",
      value: pipeline.job_attempts == null ? "—" : String(pipeline.job_attempts),
    },
  ];
}

export function askDecisionRows(decisions: AskDecisions): Row[] {
  return [
    { label: "Retrieval mode", value: decisions.retrieval_mode || "—" },
    { label: "Chunks considered", value: String(decisions.chunks_considered) },
    { label: "Sources accepted", value: String(decisions.sources_accepted) },
    { label: "Sources dropped (invented)", value: String(decisions.sources_rejected) },
    {
      label: "Prompt",
      value:
        decisions.prompt_name && decisions.prompt_version
          ? `${decisions.prompt_name}@${decisions.prompt_version}`
          : "—",
    },
    {
      label: "Model",
      value:
        decisions.provider && decisions.model
          ? `${decisions.provider}/${decisions.model}`
          : "—",
    },
    {
      label: "Latency",
      value: decisions.latency_ms == null ? "—" : `${decisions.latency_ms.toFixed(0)} ms`,
    },
    {
      label: "Tokens",
      value:
        decisions.input_tokens == null
          ? "—"
          : `${decisions.input_tokens} in / ${decisions.output_tokens ?? 0} out`,
    },
    {
      label: "Estimated cost",
      value:
        decisions.estimated_cost_usd == null
          ? "not priced"
          : `$${decisions.estimated_cost_usd.toFixed(6)}`,
    },
  ];
}
