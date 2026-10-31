# GenAI agent observability: Google ADK + Google GenAI SDK + OpenTelemetry + Dynatrace

FastAPI service running a Google ADK `LlmAgent` (Gemini) with a RAG tool. Same code on Cloud Run or GKE.

```
Browser (Dynatrace RUM) -> FastAPI + ADK agent -> OTel SDK -> OTLP/HTTP -> [Collector on GKE] -> Dynatrace
                                 |-> retrieve_docs (retrieval metrics)
                                 |-> live judge queue (Google GenAI SDK) -> eval metrics + linked eval spans
                                 |-> JSON logs with trace id -> Cloud Logging
                                 |-> (optional) metrics + logs over plain HTTP APIs -> Dynatrace   [DT_HTTP_INGEST=1]
```

Files: `app/core.py` (telemetry, RAG, evals, live judge) | `app/agent.py` (ADK agent) | `app/main.py` (API + RUM page) |
`app/dt_http.py` (hybrid HTTP ingest) | `eval/run_golden.py` (golden set + load generator) | `deploy.sh` (cloudrun|gke) | `k8s.yaml` | `Dockerfile`.

## Local
```
pip install -r requirements.txt
python -m eval.run_golden --retrieval-only
export GOOGLE_API_KEY=... OTEL_EXPORTER_OTLP_ENDPOINT=https://<env-id>.live.dynatrace.com/api/v2/otlp
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Api-Token <token>" OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta
export OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental
uvicorn app.main:app --port 8080 &  python -m eval.run_golden --load http://localhost:8080
```

## Hybrid HTTP ingest (optional)
Traces stay on OpenTelemetry (OTLP is HTTP). Metrics and logs can also be sent with direct HTTP calls:
`POST /api/v2/metrics/ingest` and `POST /api/v2/logs/ingest` (token scopes `metrics.ingest`, `logs.ingest`).
```
export DT_HTTP_INGEST=1      # credentials are taken from DT_TENANT + DT_API_TOKEN, or from the OTLP vars when they point at Dynatrace
```
On GKE the OTLP endpoint is the collector, so the `dt-otlp` secret (DT_TENANT, DT_API_TOKEN) supplies the credentials; just add `DT_HTTP_INGEST=1` to the deployment env.
Metrics appear as `agent.http.*` (groundedness, latency_ms, flagged, judge.*, feedback); logs carry `trace_id`. Sending runs on a background thread and failures never affect requests.
Curl equivalent for the demo:
```
curl -X POST "https://<env-id>.live.dynatrace.com/api/v2/metrics/ingest" -H "Authorization: Api-Token <token>" -H "Content-Type: text/plain; charset=utf-8" -d "agent.http.groundedness,env=demo gauge,0.9"
```

## Deploy
`./deploy.sh cloudrun` or `./deploy.sh gke` (env vars in the header). Cloud Run keeps CPU always allocated so the exporter and judge run between requests; it deploys without public access (`gcloud run services proxy` for the browser).

## Demo (15 min)
1 Trace: `agent.workflow` > ADK spans > `tool.retrieve_docs` (2) | 2 Dashboard: p95 `agent.workflow.latency`, tool failures, tokens, `agent.eval.groundedness` (2) |
3 Live eval: `agent.eval.judge.*`, open the linked trace (2) | 4 Break it: `TOOL_FAIL_RATE=0.4`, alert, root cause (2) | 5 Cloud Run vs GKE (2) | 6 RUM: set `DT_RUM_SCRIPT_URL` (1)

Knobs: TOOL_FAIL_RATE, JUDGE_SAMPLE_RATE, JUDGE_HALLUCINATION_THRESHOLD, MODEL, JUDGE_MODEL, DT_RUM_SCRIPT_URL, PROJECT_ID, DT_HTTP_INGEST.
Limits: online groundedness is a lexical proxy; retrieval is an in-memory TF-IDF stand-in for a vector store; SLO values are examples.
