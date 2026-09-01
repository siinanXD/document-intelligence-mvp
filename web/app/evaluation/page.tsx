"use client";

import { useEffect, useState } from "react";
import { ApiError, failureLabel, getCockpit } from "@/lib/api";
import type { CockpitSnapshot } from "@/lib/types";

export default function EvaluationPage() {
  const [cockpit, setCockpit] = useState<CockpitSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCockpit()
      .then(setCockpit)
      .catch((exc: unknown) => {
        if (exc instanceof ApiError) setError(`${failureLabel(exc.kind)}: ${exc.detail}`);
      });
  }, []);

  if (error) {
    return (
      <p className="error" role="alert">
        {error}
      </p>
    );
  }
  if (!cockpit) return <p className="muted">Loading evaluation summary…</p>;

  const tracks = [cockpit.evaluation.retrieval, cockpit.evaluation.generation].filter(Boolean);

  return (
    <div className="stack">
      <section className="panel">
        <h2>Evaluation gates</h2>
        <p className="muted">
          Checked-in hashing/scripted baselines. A live model run is opt-in and is not this
          summary.
        </p>
      </section>
      {tracks.map((track) => (
        <section className="panel" key={track!.dataset}>
          <h2>
            {track!.dataset} {track!.dataset_version}
          </h2>
          <p className={track!.gate_passed ? "banner ok" : "banner error"}>
            {track!.gate_passed ? "Release gate passed" : "Release gate failed"}
            {track!.leakage ? ` · leakage ${track!.leakage}` : ""}
          </p>
          <pre>{JSON.stringify(track!.summary, null, 2)}</pre>
        </section>
      ))}
    </div>
  );
}
