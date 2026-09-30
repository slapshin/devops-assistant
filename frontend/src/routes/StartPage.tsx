import { useQuery } from "@tanstack/react-query";
import { apiGet, type RuntimeConfig } from "../api/client";
import { NotImplemented } from "../components/Layout";

export function StartPage() {
  const config = useQuery({
    queryKey: ["config"],
    queryFn: () => apiGet<RuntimeConfig>("/api/config"),
  });

  return (
    <NotImplemented view="Start" task="T008 (scope selection, Analyze, recent reports)">
      <h2>Backend connection</h2>
      {config.isPending && <p>Loading configuration…</p>}
      {config.isError && (
        <p role="alert">
          Backend unavailable: {config.error.message}{" "}
          <button type="button" onClick={() => void config.refetch()}>
            Retry
          </button>
        </p>
      )}
      {config.data && (
        <dl>
          <dt>Metrics source</dt>
          <dd>{config.data.metrics_source}</dd>
          <dt>Detector</dt>
          <dd>
            {config.data.detector_version} ({config.data.config_hash})
          </dd>
          <dt>AI explanations</dt>
          <dd>
            {config.data.ai_provider} · {config.data.explanation_status}
          </dd>
        </dl>
      )}
    </NotImplemented>
  );
}
