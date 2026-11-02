# GenAI agent observability: Google ADK + Google GenAI SDK + OpenTelemetry + Dynatrace

**Live demo:** https://genai-agent-observability-demo-knmgbkuno8snohhdgvfcbs.streamlit.app/ (free tier: may take a moment to wake up)

A Google ADK `LlmAgent` (Gemini) with a RAG tool, a Streamlit front end, and OpenTelemetry traces, metrics and logs exported to Dynatrace.
The same agent can also run as a FastAPI service on Cloud Run or GKE (see the end of this file).

```
Streamlit UI (app/ui.py) -> ADK agent -> OTel SDK -> OTLP/HTTP -> Dynatrace
                              |-> retrieve_docs (retrieval metrics)
                              |-> model fallback (timeout/404/429/5xx) -> extractive answer if all models fail
                              |-> live judge queue (Google GenAI SDK) -> eval metrics + linked eval spans
                              |-> JSON logs with trace id
```

Files: `app/ui.py` (Streamlit UI) | `app/agent.py` (ADK agent, fallback, metrics) | `app/core.py` (telemetry, RAG, evals, judge) |
`app/main.py` (optional FastAPI API) | `app/dt_http.py` (optional HTTP ingest) | `eval/run_golden.py` (golden set) | `deploy.sh`, `Dockerfile`, `k8s.yaml` (Cloud Run / GKE).

## Run locally
```
pip install -r requirements.txt
export GOOGLE_API_KEY=...            # PowerShell: $env:GOOGLE_API_KEY="..."
python -m streamlit run app/ui.py
```
With no OTLP endpoint set, no telemetry is exported (no errors either). Check retrieval quality without any key: `python -m eval.run_golden --retrieval-only`.

## Deploy on Streamlit Community Cloud (free)
1. Sign in at share.streamlit.io with GitHub and authorize access to this repository (private repos need that authorization).
2. Create app: repository `Vinay0997/genai-agent-observability-demo`, branch `main`, main file `app/ui.py`, Python 3.12.
3. Paste your values into App settings > Secrets, using `.streamlit/secrets.toml.example` as the template. Top-level secrets are also exposed as environment variables, which is how the app reads them.
4. Deploy. Never commit real keys or tokens.

## Telemetry to Dynatrace
Set `OTEL_EXPORTER_OTLP_ENDPOINT` to `https://<env-id>.live.dynatrace.com/api/v2/otlp`, `OTEL_EXPORTER_OTLP_HEADERS` to `Authorization=Api-Token <token>`,
and metrics temporality to `delta`. The token needs scopes to ingest OpenTelemetry traces, metrics and logs.

## Reliability behavior (good to show in the demo)
- Each model attempt has a timeout (`ATTEMPT_TIMEOUT`, 20s). On timeout, 404, 429 or 5xx the agent tries `FALLBACK_MODEL`.
- If every model fails, the answer comes from the top retrieved passage (`degraded=true`, span attribute `llm.degraded`).
- Failures show up as `agent.llm.errors{model,code}` and `llm.error` span events.

## Demo (15 min)
1 Trace: `agent.workflow` > ADK spans > `tool.retrieve_docs` | 2 Dashboard: p95 `agent.workflow.latency`, tool failures, tokens, `agent.eval.groundedness` |
3 Live eval: `agent.eval.judge.*`, open the linked trace | 4 Break it: raise the tool failure slider in the sidebar, watch the alert, find root cause | 5 Model timeout/fallback: set `MODEL` to a slow model and show `agent.llm.errors`.

Knobs: MODEL, FALLBACK_MODEL, JUDGE_MODEL, ATTEMPT_TIMEOUT, EXTRACTIVE_FALLBACK, FLAG_THRESHOLD, TOOL_FAIL_RATE, JUDGE_SAMPLE_RATE, JUDGE_HALLUCINATION_THRESHOLD.
Limits: online groundedness is a lexical proxy; retrieval is an in-memory TF-IDF stand-in for a vector store; SLO values are examples.

## Optional: Cloud Run / GKE
`./deploy.sh cloudrun` (or `gke`) deploys `app/main.py` (FastAPI: `/ask`, `/feedback`, `/healthz`) from the Dockerfile; see `deploy.sh` for the variables it needs (`PROJECT`, `GOOGLE_API_KEY`, `DT_TENANT`, `DT_API_TOKEN`).
