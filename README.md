# GenAI agent observability demo: Google ADK + Google GenAI SDK + OpenTelemetry + Dynatrace

- Agent: Google ADK `LlmAgent` (Gemini) with a `search_kb` tool, run through ADK `Runner`, served by FastAPI.
- Google GenAI SDK: used as an async LLM judge (groundedness/relevance) on sampled requests.
- Telemetry: ADK's built-in OpenTelemetry spans plus custom metrics -> OTLP/HTTP -> Collector -> Dynatrace.

## Run locally
```
export GOOGLE_API_KEY=<gemini key>
export OTEL_EXPORTER_OTLP_ENDPOINT=https://<env-id>.live.dynatrace.com/api/v2/otlp
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Api-Token <token>"
export OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta
export OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental
pip install -r requirements.txt && uvicorn app.main:app --port 8080
python loadgen.py
```

## GKE
Build and push the image, then `kubectl apply -f k8s/otel-collector.yaml -f k8s/deployment.yaml`.
ADK also offers `adk deploy gke` as a built-in path.

## Knobs
TOOL_FAIL_RATE (inject tool failures for the demo), JUDGE_SAMPLE_RATE, MODEL, JUDGE_MODEL.
