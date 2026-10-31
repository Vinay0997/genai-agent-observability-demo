# GenAI agent observability: Google ADK + Google GenAI SDK + OpenTelemetry + Dynatrace (Streamlit)

Streamlit chat app running a Google ADK `LlmAgent` (Gemini) with a RAG tool. Runs locally, on Cloud Run, or on GKE.

```
Streamlit UI -> ADK agent -> OTel SDK -> OTLP/HTTP -> [Collector on GKE] -> Dynatrace
                  |-> retrieve_docs (retrieval metrics)
                  |-> live judge queue (Google GenAI SDK) -> eval metrics + linked eval spans
                  |-> JSON logs with trace id -> Cloud Logging
                  |-> (optional) metrics + logs over plain HTTP APIs -> Dynatrace   [DT_HTTP_INGEST=1]
```

Files: `app/ui.py` (Streamlit UI) | `app/agent.py` (ADK agent) | `app/core.py` (telemetry, RAG, evals, live judge) |
`app/dt_http.py` (hybrid HTTP ingest) | `eval/run_golden.py` (golden set + traffic generator) | `deploy.sh` (cloudrun|gke) | `k8s.yaml` | `Dockerfile`.
(`app/main.py` and the other FastAPI-era files are no longer used.)

## Local
```
pip install -r requirements.txt
python -m eval.run_golden --retrieval-only
export GOOGLE_API_KEY=... OTEL_EXPORTER_OTLP_ENDPOINT=https://<env-id>.live.dynatrace.com/api/v2/otlp
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Api-Token <token>" OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta
export OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental
python -m streamlit run app/ui.py --server.port 8080
python -m eval.run_golden --repeat 20 --sleep 2     # optional traffic for dashboards
```

## Deploy
`./deploy.sh cloudrun` or `./deploy.sh gke` (env vars in the header). Cloud Run: CPU always allocated, one instance with session affinity (Streamlit websockets), private service.
Open it with `gcloud run services proxy adk-agent-demo --region us-central1 --port 8080`, then Web Preview on port 8080 in Cloud Shell.

## Hybrid HTTP ingest (optional)
Traces stay on OpenTelemetry (OTLP is HTTP). Set `DT_HTTP_INGEST=1` to also send metrics and logs through
`POST /api/v2/metrics/ingest` and `POST /api/v2/logs/ingest` (token scopes `metrics.ingest`, `logs.ingest`).
Credentials come from `DT_TENANT` + `DT_API_TOKEN`, or from the OTLP variables when they point at Dynatrace. Metrics appear as `agent.http.*`; logs carry `trace_id`.

## Demo (15 min)
1 Trace: `agent.workflow` > ADK spans > `tool.retrieve_docs` (2) | 2 Dashboard: p95 `agent.workflow.latency`, tool failures, tokens, `agent.eval.groundedness` (2) |
3 Live eval: `agent.eval.judge.*`, open the linked trace (2) | 4 Break it: drag the sidebar "Tool failure rate" slider up, alert, root cause (2) | 5 Cloud Run vs GKE (2)

Knobs: TOOL_FAIL_RATE, JUDGE_SAMPLE_RATE, JUDGE_HALLUCINATION_THRESHOLD, MODEL, JUDGE_MODEL, PROJECT_ID, DT_HTTP_INGEST.
Limits: online groundedness is a lexical proxy; retrieval is an in-memory TF-IDF stand-in for a vector store; SLO values are examples.
