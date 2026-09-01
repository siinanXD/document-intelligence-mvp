import type { Pipeline } from "@/lib/types";
import { stageLabel } from "@/lib/pipeline";
import { failureLabel } from "@/lib/api";

export function PipelineTimeline({ pipeline }: { pipeline: Pipeline }) {
  return (
    <section className="panel">
      <h2>Pipeline</h2>
      <ol className="timeline">
        {pipeline.stages.map((stage) => (
          <li
            key={stage.id}
            className={
              stage.complete ? "done" : stage.current ? "current" : "pending"
            }
          >
            <strong>{stageLabel(stage.id)}</strong>
            <span>
              {stage.complete
                ? "complete"
                : stage.current
                  ? pipeline.failed
                    ? "failed here"
                    : "current"
                  : "waiting"}
            </span>
          </li>
        ))}
      </ol>
      {pipeline.failed ? (
        <p className="error" role="alert">
          Processing failed ({failureLabel(pipeline.failure_class)}
          {pipeline.error_type ? `: ${pipeline.error_type}` : ""}).
        </p>
      ) : null}
    </section>
  );
}
