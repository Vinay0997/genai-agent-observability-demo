"""Telemetry setup, RAG retrieval, evaluation (proxy + Gemini judge) and the live-eval worker."""
import asyncio, json, math, os, random, re, time
from collections import Counter
from opentelemetry import trace, metrics

tracer = trace.get_tracer("app")
meter = metrics.get_meter("app")


def setup(service="adk-agent-demo"):
    """Call BEFORE importing ADK so its built-in spans use these providers.
    Endpoint/headers come from OTEL_EXPORTER_OTLP_*; Dynatrace needs delta metrics temporality."""
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    res = Resource.create({"service.name": service, "deployment.environment": os.getenv("ENV", "demo"),
                           "k8s.pod.name": os.getenv("HOSTNAME", "local")})
    tp = TracerProvider(resource=res)
    tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tp)
    metrics.set_meter_provider(MeterProvider(resource=res, metric_readers=[
        PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=15000)]))


# ---------- RAG (small in-memory TF-IDF index standing in for a vector store) ----------
DOCS = {
    "slo-basics": "An SLO is a target for a service level indicator, for example p95 latency under 2 seconds or 99.5 percent availability. An error budget is the allowed amount of failure.",
    "otlp-dynatrace": "Dynatrace ingests OpenTelemetry traces, metrics and logs over OTLP/HTTP at /api/v2/otlp using an API token with ingest scopes. Metrics must use delta temporality.",
    "cloudrun-observability": "On Cloud Run the app exports OTLP directly to Dynatrace. Use CPU always allocated so background exporters keep running, and store tokens in Secret Manager.",
    "gke-observability": "On GKE deploy an OpenTelemetry Collector for OTLP export and optionally the Dynatrace Operator with a DynaKube for pod, node and topology context.",
    "adk-tracing": "Google ADK emits OpenTelemetry spans for invoke_agent, generate_content and execute_tool following GenAI semantic conventions. Set global providers before importing ADK.",
    "genai-evaluation": "Groundedness measures whether claims in an answer are supported by retrieved context. Retrieval quality is measured with hit rate and MRR against a golden set. Hallucination is flagged when groundedness is low.",
    "rum": "Dynatrace Real User Monitoring uses a JavaScript tag in the page head to capture page loads, user actions and errors, and correlates them with backend traces.",
}
STOP = set("the a an of to in on for and or is are be by with at as it that this from how what does do can".split())
_tok = lambda s: [t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in STOP]
_df = Counter(t for d in DOCS.values() for t in set(_tok(d)))
_idf = lambda t: math.log((1 + len(DOCS)) / (1 + _df.get(t, 0))) + 1
_vecs = {k: Counter(_tok(v)) for k, v in DOCS.items()}
ret_latency = meter.create_histogram("agent.retrieval.latency", unit="ms")
ret_top = meter.create_histogram("agent.retrieval.top_score")
ret_docs = meter.create_histogram("agent.retrieval.docs")


def _cos(q, d):
    num = sum(q[t] * d[t] * _idf(t) ** 2 for t in q if t in d)
    nq = math.sqrt(sum((c * _idf(t)) ** 2 for t, c in q.items()))
    nd = math.sqrt(sum((c * _idf(t)) ** 2 for t, c in d.items()))
    return num / (nq * nd) if nq and nd else 0.0


def search(query, k=3, min_score=0.05):
    t0 = time.time()
    q = Counter(_tok(query))
    ranked = sorted(((_cos(q, v), key) for key, v in _vecs.items()), reverse=True)
    hits = [{"id": key, "text": DOCS[key], "score": round(s, 3)} for s, key in ranked[:k] if s >= min_score]
    ret_latency.record((time.time() - t0) * 1000)
    ret_top.record(hits[0]["score"] if hits else 0.0)
    ret_docs.record(len(hits))
    return hits


# ---------- Evaluation ----------
def _words(s): return set(re.findall(r"[a-z0-9]+", s.lower()))


def groundedness_proxy(answer, contexts):
    a = {t for t in _words(answer) if len(t) > 3}
    c = set().union(*[_words(x) for x in contexts]) if contexts else set()
    return len(a & c) / len(a) if a else 0.0


def relevance_proxy(question, answer):
    q = {t for t in _words(question) if len(t) > 3}
    return len(q & _words(answer)) / len(q) if q else 0.0


def hit_and_rr(retrieved_ids, expected_ids):
    for i, rid in enumerate(retrieved_ids, 1):
        if rid in expected_ids:
            return 1.0, 1.0 / i
    return 0.0, 0.0


JUDGE_PROMPT = """You are a strict evaluator. Given CONTEXT, QUESTION and ANSWER, return JSON only:
{{"groundedness": <0-1, fraction of claims supported by CONTEXT>, "relevance": <0-1>}}
CONTEXT: {ctx}
QUESTION: {q}
ANSWER: {a}"""


async def llm_judge(question, answer, contexts):
    """Google GenAI SDK as LLM judge; always run off the request path."""
    from google import genai
    r = await genai.Client().aio.models.generate_content(
        model=os.getenv("JUDGE_MODEL", "gemini-2.5-flash"),
        contents=JUDGE_PROMPT.format(ctx=" | ".join(contexts), q=question, a=answer),
        config={"response_mime_type": "application/json"})
    return json.loads(r.text)


# ---------- Live evaluation worker ----------
j_ground = meter.create_histogram("agent.eval.judge.groundedness")
j_rel = meter.create_histogram("agent.eval.judge.relevance")
j_halluc = meter.create_counter("agent.eval.judge.hallucination")
j_err = meter.create_counter("agent.eval.judge.errors")
SAMPLE = float(os.getenv("JUDGE_SAMPLE_RATE", "0.2"))
THRESH = float(os.getenv("JUDGE_HALLUCINATION_THRESHOLD", "0.7"))
queue: asyncio.Queue = asyncio.Queue(maxsize=200)


def log(event, **kw):
    """Structured JSON to stdout; Cloud Run/GKE forward it to Cloud Logging with the trace id."""
    proj = os.getenv("PROJECT_ID")
    if proj and kw.get("trace_id"):
        kw["logging.googleapis.com/trace"] = f"projects/{proj}/traces/{kw['trace_id']}"
    print(json.dumps({"event": event, "severity": "INFO", **kw}), flush=True)


def maybe_enqueue(rec):
    if random.random() < SAMPLE and not queue.full():
        queue.put_nowait(rec)


async def worker():
    """Judge sampled requests; the eval span carries a Link to the original request trace."""
    from opentelemetry.trace import Link
    while True:
        rec = await queue.get()
        try:
            links = [Link(rec["span_ctx"])] if rec.get("span_ctx") else None
            with tracer.start_as_current_span("eval.judge", links=links) as sp:
                res = await llm_judge(rec["question"], rec["answer"], rec["contexts"])
                g, r = float(res.get("groundedness", 0)), float(res.get("relevance", 0))
                j_ground.record(g)
                j_rel.record(r)
                bad = g < THRESH
                if bad:
                    j_halluc.add(1)
                sp.set_attribute("eval.groundedness", g)
                sp.set_attribute("eval.relevance", r)
                sp.set_attribute("eval.original_trace_id", rec["trace_id"])
                log("judge_result", trace_id=rec["trace_id"], groundedness=g, relevance=r, hallucination=bad)
        except Exception as e:
            j_err.add(1)
            log("judge_error", error=str(e)[:200])
