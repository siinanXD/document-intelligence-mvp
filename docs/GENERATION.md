# Generation controls, metadata and observability

SIN-76 makes every current (and future) AI generation bounded, reproducible,
versioned, measurable, traceable, cost-aware and privacy-safe. It does not
evaluate answer quality (SIN-74) and it does not add machine-intelligence
parsers.

## Generation settings

Read through `app/core/settings.py`. Defaults are explicit; they are not
whatever the OpenAI SDK currently happens to use.

| Setting | Default | Meaning |
|---|---|---|
| `LLM_TIMEOUT_SECONDS` | `30` | Per-request timeout on `chat.completions.create` / `.parse` |
| `LLM_MAX_RETRIES` | `2` | Additional attempts after the first, for transient failures only |
| `LLM_MAX_OUTPUT_TOKENS` | `1024` | `max_tokens` on every generation call |
| `LLM_TEMPERATURE` | `0.0` | Deterministic sampling. `top_p`, `seed` and penalties are not sent |
| `LLM_INPUT_USD_PER_MILLION` | unset | Input price used for `estimated_cost_usd` |
| `LLM_OUTPUT_USD_PER_MILLION` | unset | Output price used for `estimated_cost_usd` |

The OpenAI client is constructed with `max_retries=0`. Application retries do
not stack on SDK retries. Retries use SDK exception types (`APITimeoutError`,
`APIConnectionError`, `RateLimitError`, `InternalServerError`, other 5xx).
Invalid structured output (`parsed=None`) and 4xx client errors are not retried.

`gpt-4o-mini` accepts `temperature` and `max_tokens`. Unsupported sampling
knobs are omitted rather than invented.

## Prompt identity

Every production generation prompt is a `Prompt` (`name`, `version`, `system`)
in `app/providers/prompts.py`:

* `ask_grounded` / `v1` — grounded `/ask`
* `document_profile` / `v1` — worker profile extraction

Changing the instructions requires changing `version` (and the frozen hash in
`tests/test_prompts.py`). Future prompts such as `component_extraction` add
another constant; they do not need a new generation stack.

## Generation envelope

`LLMProvider.complete` and `complete_structured` return `GenerationResult`:

* `content` — string or parsed Pydantic model (structured output is not flattened)
* `provider`, `model`
* `prompt_name`, `prompt_version`
* `latency_ms`
* `input_tokens`, `output_tokens`, `total_tokens` — from the provider `usage`
  object when present; never estimated from string length
* `estimated_cost_usd` — `None` when prices or usage are missing; never reported
  as `0` because the cost is unknown
* `finish_reason`
* `retry_count` — retries that actually ran before success
* `trace_id`, `request_id`

`/ask` JSON is unchanged. Envelope and retrieval metadata stay internal.

## Retrieval metadata

For `/ask`, `AskResult.retrieval` records mode, candidate/supplied counts, and
per-hit `source_id`, `document_id`, rank and score. Chunk text is not stored
in production-safe traces.

## Correlation

HTTP generations reuse the privacy-safe `X-Request-Id` from request logging.
Worker generations bind `job_id` and `document_id`. `trace_id` is the request
id if present, else the job id, else a fresh uuid.

## Tracing

`TracingAdapter` / `NullTracingAdapter` / `LangfuseTracingAdapter`.

* `TRACING_PROVIDER=none` (default): no-op
* `TRACING_PROVIDER=langfuse`: optional adapter, credentials required
* Missing package, missing credentials, SDK errors and timeouts never fail the
  customer request
* Vendor imports stay in `app/providers/langfuse_adapter.py`
* Install with `pip install -e ".[observability]"` when you want the SDK

Langfuse is not a startup dependency and is not installed in ordinary CI.

## Privacy

Default traces and logs carry identifiers and measurements only. They do not
include document bodies, chunk text, complete prompts, questions, answers,
Authorization headers, API keys, cookies or uploaded bytes.

`TRACING_CAPTURE_CONTENT=true` is development-only content capture. It is off
by default, named explicitly, and is not implied by `LOG_LEVEL=DEBUG` or any
other debug flag. See `docs/PRIVACY.md`.

## SIN-74

`app.evaluation.generation.generation_eval_record` projects an `AskResult` into
the fields SIN-74 will persist (case id, prompt identity, provider/model,
tokens, latency, cost, cited source ids, finish status, trace id). This
milestone does not score answers.
