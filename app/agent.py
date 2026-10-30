import asyncio, contextvars, os, random, time, uuid
from opentelemetry import trace, metrics
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from .evals import groundedness_proxy, relevance_proxy, llm_judge

tracer = trace.get_tracer("agent")
meter = metrics.get_meter("agent")
tool_calls = meter.create_counter("agent.tool.calls")
tool_fail = meter.create_counter("agent.tool.failures")
workflows = meter.create_counter("agent.workflow.completed")
wf_latency = meter.create_histogram("agent.workflow.latency", unit="ms")
tok_in = meter.create_counter("agent.llm.tokens.input")
tok_out = meter.create_counter("agent.llm.tokens.output")
ground_h = meter.create_histogram("agent.eval.groundedness")
rel_h = meter.create_histogram("agent.eval.relevance")
halluc = meter.create_counter("agent.eval.hallucination_flagged")
judge_h = meter.create_histogram("agent.eval.judge.groundedness")

MODEL = os.getenv("MODEL", "gemini-2.5-flash")
FAIL_RATE = float(os.getenv("TOOL_FAIL_RATE", "0.05"))
JUDGE_SAMPLE = float(os.getenv("JUDGE_SAMPLE_RATE", "0.2"))
THRESHOLD = 0.8

KB = {"slo": "An SLO is a target for a service level indicator such as p95 latency under 2 seconds or 99.5 percent availability.",
      "otel": "OpenTelemetry exports traces, metrics and logs over OTLP to backends such as Dynatrace.",
      "gke": "On GKE the Dynatrace Operator deploys OneAgent and ActiveGate; on Cloud Run the app exports OTLP directly."}

_ctx = contextvars.ContextVar("retrieved", default=None)


def search_kb(topic: str) -> dict:
    """Look up internal knowledge base notes about a topic such as slo, otel or gke.

    Args:
        topic: Short keyword to search for.
    Returns:
        dict with status and a list of matching notes.
    """
    with tracer.start_as_current_span("tool.search_kb") as sp:
        tool_calls.add(1, {"tool": "search_kb"})
        if random.random() < FAIL_RATE:
            tool_fail.add(1, {"tool": "search_kb"})
            sp.set_status(trace.Status(trace.StatusCode.ERROR, "kb timeout"))
            return {"status": "error", "notes": []}
        notes = [v for k, v in KB.items() if k in topic.lower()]
        sp.set_attribute("retrieval.docs", len(notes))
        bucket = _ctx.get()
        if bucket is not None:
            bucket.extend(notes)
        return {"status": "ok", "notes": notes}


root_agent = LlmAgent(
    name="sre_helper", model=MODEL,
    instruction="Answer ONLY from notes returned by the search_kb tool. If no notes are found, say you do not know.",
    tools=[search_kb])

session_service = InMemorySessionService()
runner = Runner(agent=root_agent, app_name="adk_obs_demo", session_service=session_service)


async def _judge(q, a, ctx):
    try:
        res = await llm_judge(q, a, ctx)
        judge_h.record(float(res.get("groundedness", 0)))
    except Exception:
        pass


async def run(question: str, user_id: str = "demo"):
    sid = uuid.uuid4().hex
    await session_service.create_session(app_name="adk_obs_demo", user_id=user_id, session_id=sid)
    bucket = []
    _ctx.set(bucket)
    t0 = time.time()
    answer, ti, to = "", 0, 0
    with tracer.start_as_current_span("agent.workflow") as sp:
        msg = types.Content(role="user", parts=[types.Part(text=question)])
        async for ev in runner.run_async(user_id=user_id, session_id=sid, new_message=msg):
            um = getattr(ev, "usage_metadata", None)
            if um:
                ti += um.prompt_token_count or 0
                to += um.candidates_token_count or 0
            if ev.is_final_response() and ev.content and ev.content.parts:
                answer = ev.content.parts[0].text or ""
        tok_in.add(ti, {"model": MODEL})
        tok_out.add(to, {"model": MODEL})
        g, r = groundedness_proxy(answer, bucket), relevance_proxy(question, answer)
        flagged = g < THRESHOLD
        ground_h.record(g)
        rel_h.record(r)
        if flagged:
            halluc.add(1)
        sp.set_attribute("eval.groundedness", g)
        sp.set_attribute("eval.relevance", r)
        sp.set_attribute("eval.hallucination_flagged", flagged)
        ms = (time.time() - t0) * 1000
        wf_latency.record(ms)
        workflows.add(1, {"status": "success" if bucket else "degraded"})
    if random.random() < JUDGE_SAMPLE:
        asyncio.create_task(_judge(question, answer, bucket))
    return {"answer": answer, "groundedness": round(g, 2), "relevance": round(r, 2), "flagged": flagged, "latency_ms": round(ms)}
