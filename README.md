# GenAI agent observability: Google ADK + Google GenAI SDK + OpenTelemetry + Dynatrace

FastAPI service running a Google ADK `LlmAgent` (Gemini) with a RAG tool. Same code on Cloud Run or GKE.

```
Browser (Dynatrace RUM) -> FastAPI + ADK agent -> OTel SDK -> OTLP/HTTP -> [Collector on GKE] -> Dynatrace
                                 |-> retrieve_docs (retrieval metrics)
                                 |-> live judge queue (Google GenAI SDK) -> eval metrics + linked eval spans
                                 |-> JSON logs with trace id -> Cloud Logging
```

Files: `app/core.py` (telemetry, RAG, evals, live judge) | `app/agent.py` (ADK agent) | `app/main.py` (API + RUM page) |
`eval/run_golden.py` (golden set + load generator) | `deploy.sh` (cloudrun|gke) | `k8s.yaml` | `Dockerfile`.

## Local
```
pip install -r requirements.txt
python -m eval.run_golden --retrieval-only
export GOOGLE_API_KEY=... OTEL_EXPORTER_OTLP_ENDPOINT=https://<env-id>.live.dynatrace.com/api/v2/otlp
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Api-Token <token>" OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta
export OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental
uvicorn app.main:app --port 8080 &  python -m eval.run_golden --load http://localhost:8080
```

## Deploy
`./deploy.sh cloudrun` or `./deploy.sh gke` (env vars in the header). Cloud Run keeps CPU always allocated so the exporter and judge run between requests; it deploys without public access (`gcloud run services proxy` for the browser).

## Demo (15 min)
1 Trace: `agent.workflow` > ADK spans > `tool.retrieve_docs` (2) | 2 Dashboard: p95 `agent.workflow.latency`, tool failures, tokens, `agent.eval.groundedness` (2) |
3 Live eval: `agent.eval.judge.*`, open the linked trace (2) | 4 Break it: `TOOL_FAIL_RATE=0.4`, alert, root cause (2) | 5 Cloud Run vs GKE (2) | 6 RUM: set `DT_RUM_SCRIPT_URL` (1)

Knobs: TOOL_FAIL_RATE, JUDGE_SAMPLE_RATE, JUDGE_HALLUCINATION_THRESHOLD, MODEL, JUDGE_MODEL, DT_RUM_SCRIPT_URL, PROJECT_ID.
Limits: online groundedness is a lexical proxy; retrieval is an in-memory TF-IDF stand-in for a vector store; SLO values are examples.
