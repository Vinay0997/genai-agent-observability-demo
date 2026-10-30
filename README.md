# GenAI agent observability demo (Dynatrace + OpenTelemetry)

Agent (FastAPI) -> OTel SDK -> OTLP/HTTP -> Collector -> Dynatrace. Runs locally, on Cloud Run, or on GKE.

## 1. Local (5 min)
```
export DT_TENANT=<env-id>  DT_API_TOKEN=<token>
export OTEL_EXPORTER_OTLP_ENDPOINT=https://$DT_TENANT.live.dynatrace.com/api/v2/otlp
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Api-Token $DT_API_TOKEN"
export OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta
pip install -r requirements.txt && uvicorn app.main:app --port 8080
python loadgen.py          # separate terminal
```
Set GOOGLE_API_KEY to use real Gemini; otherwise a mock LLM runs.

## 2. GKE
```
docker build -t REGION-docker.pkg.dev/PROJECT/repo/adk-agent-demo:v1 . && docker push REGION-docker.pkg.dev/PROJECT/repo/adk-agent-demo:v1
kubectl apply -f k8s/otel-collector.yaml -f k8s/deployment.yaml
python loadgen.py http://<EXTERNAL-IP>
```
Optional: install the Dynatrace Operator (DynaKube) for pod/node views.

## 3. Demo flow (10 min)
1. Architecture slide.
2. Traces: agent.workflow -> tool.retrieve -> gen_ai.chat.
3. Dashboard: p95 latency, tokens, tool failure rate, groundedness, hallucination flags.
4. SLO + alert: groundedness < 0.8 / p95 > 2s.
5. Break it (raise tool failure rate), show root cause from a trace.
6. Cloud Run vs GKE slide.
