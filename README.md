# GenAI agent observability: Google ADK + Google GenAI SDK + OpenTelemetry + Dynatrace

FastAPI service running a Google ADK `LlmAgent` (Gemini) with a RAG tool. Deployed to Google Cloud Run (or GKE). Streamlit is no longer used.

```
Client -> FastAPI (/ask) + ADK agent -> OTel SDK -> OTLP/HTTP -> [Collector on GKE] -> Dynatrace
               |-> retrieve_docs (retrieval metrics)
               |-> live judge queue (Google GenAI SDK) -> eval metrics + linked eval spans
               |-> JSON logs with trace id -> Cloud Logging
               |-> (optional) metrics + logs over plain HTTP APIs -> Dynatrace   [DT_HTTP_INGEST=1]
```

Files: `app/main.py` (API: /ask, /feedback, /healthz, small test page at /) | `app/agent.py` (ADK agent) | `app/core.py` (telemetry, RAG, evals, live judge) |
`app/dt_http.py` (hybrid HTTP ingest) | `eval/run_golden.py` (golden set + traffic generator) | `deploy.sh` (cloudrun|gke) | `k8s.yaml` | `Dockerfile`.
(`app/ui.py` is the old Streamlit UI and is unused.)

## Deploy to Cloud Run (Cloud Shell)
```
export PROJECT=<project-id> GOOGLE_API_KEY=<gemini key> DT_TENANT=<env-id> DT_API_TOKEN=<dt0c01 token>
chmod +x deploy.sh && ./deploy.sh cloudrun
```
The service is private (no public access), with CPU always allocated so the exporter and judge run between requests.

## Test the Cloud Run service
```
URL=$(gcloud run services describe adk-agent-demo --region us-central1 --format='value(status.url)')
curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" "$URL/ask?q=what+is+an+slo"
ID_TOKEN=$(gcloud auth print-identity-token) python3 -m eval.run_golden --url $URL --repeat 20 --sleep 2    # traffic
```
The `/ask` response has `answer`, `trace_id`, `groundedness`, `latency_ms`, `sources`. To use the small test page in a browser: `gcloud run services proxy adk-agent-demo --region us-central1 --port 8080`.

## Local
```
pip install -r requirements.txt
python -m eval.run_golden --retrieval-only
export GOOGLE_API_KEY=... MODEL=gemini-3.8-flash JUDGE_MODEL=gemini-3.8-flash
export OTEL_EXPORTER_OTLP_ENDPOINT=https://<env-id>.live.dynatrace.com/api/v2/otlp
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Api-Token <token>" OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta
export OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental
uvicorn app.main:app --port 8080
```

## Hybrid HTTP ingest (optional)
Traces stay on OpenTelemetry (OTLP is HTTP). Set `DT_HTTP_INGEST=1` to also send metrics and logs through
`POST /api/v2/metrics/ingest` and `POST /api/v2/logs/ingest` (token scopes `metrics.ingest`, `logs.ingest`).
Credentials come from `DT_TENANT` + `DT_API_TOKEN`, or from the OTLP variables when they point at Dynatrace. Metrics appear as `agent.http.*`; logs carry `trace_id`.

## Demo (15 min)
1 Trace: `agent.workflow` > ADK spans > `tool.retrieve_docs` (2) | 2 Dashboard: p95 `agent.workflow.latency`, tool failures, tokens, `agent.eval.groundedness` (2) |
3 Live eval: `agent.eval.judge.*`, open the linked trace (2) | 4 Break it: `gcloud run services update adk-agent-demo --region us-central1 --update-env-vars TOOL_FAIL_RATE=0.4`, alert, root cause (2) | 5 Cloud Run vs GKE (2)

Knobs: MODEL, JUDGE_MODEL, TOOL_FAIL_RATE, JUDGE_SAMPLE_RATE, JUDGE_HALLUCINATION_THRESHOLD, PROJECT_ID, DT_HTTP_INGEST, DT_RUM_SCRIPT_URL.
Limits: online groundedness is a lexical proxy; retrieval is an in-memory TF-IDF stand-in for a vector store; SLO values are examples.
