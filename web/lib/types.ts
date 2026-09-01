export type FailureKind =
  | "configuration"
  | "provider"
  | "database"
  | "vector_store"
  | "document_processing"
  | "unknown";

export type DocumentRecord = {
  id: string;
  filename: string;
  mime_type: string;
  file_hash: string;
  status: "queued" | "processing" | "ready" | "failed";
  title: string | null;
  document_type: string | null;
  parser_name: string | null;
  embedding_provider: string | null;
  embedding_model: string | null;
  embedding_version: string | null;
  created_at: string;
  updated_at: string;
};

export type PipelineStage = {
  id: string;
  complete: boolean;
  current: boolean;
};

export type Pipeline = {
  stages: PipelineStage[];
  current_stage: string;
  failed: boolean;
  failure_class: FailureKind | string | null;
  error_type: string | null;
  chunk_count: number;
  parser_name: string | null;
  embedding_provider: string | null;
  embedding_model: string | null;
  embedding_version: string | null;
  job_status: string | null;
  job_attempts: number | null;
};

export type SearchHit = {
  chunk_id: string;
  document_id: string;
  filename: string;
  source_id: string;
  text: string;
  score: number;
  ordinal: number;
  page_number: number | null;
  section_title: string | null;
};

export type AskDecisions = {
  retrieval_mode: string | null;
  chunks_considered: number;
  sources_accepted: number;
  sources_rejected: number;
  prompt_name: string | null;
  prompt_version: string | null;
  provider: string | null;
  model: string | null;
  latency_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  estimated_cost_usd: number | null;
  finish_reason: string | null;
};

export type AskResult = {
  answer: string;
  has_sufficient_evidence: boolean;
  conflicting: boolean;
  considered: number;
  sources: Array<{
    source_id: string;
    document_id: string;
    filename: string;
    page_number: number | null;
    section_title: string | null;
    text: string;
    score: number;
  }>;
  decisions: AskDecisions;
};

export type SourceDetail = {
  source_id: string;
  document_id: string;
  filename: string;
  ordinal: number;
  text: string;
  page_number: number | null;
  section_title: string | null;
};

export type Relation = {
  relation_type: string;
  score: number | null;
  reason: Record<string, unknown>;
  target: {
    document_id: string;
    filename: string;
    title: string | null;
    document_type: string | null;
  };
};

export type CockpitSnapshot = {
  status: string;
  version: string;
  environment: string;
  checks: Record<string, string>;
  providers: {
    embedding_provider: string;
    embedding_model: string;
    embedding_version: string;
    llm_provider: string;
    llm_model: string;
    embedding_configured: boolean;
    llm_configured: boolean;
  };
  evaluation: {
    retrieval: EvaluationTrack | null;
    generation: EvaluationTrack | null;
  };
};

export type EvaluationTrack = {
  dataset: string;
  dataset_version: string;
  track: string;
  gate_passed: boolean;
  leakage: number;
  summary: Record<string, unknown>;
  thresholds: Record<string, unknown>;
};
