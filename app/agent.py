"""Google ADK agent with a RAG tool, plus the per-request workflow that records metrics and eval scores."""
import contextvars, os, random, time, uuid
from opentelemetry import trace, metrics
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types, errors as genai_errors
from . import core

tracer = trace.get_tracer("agent")
meter = metrics.get_meter("agent")
tool_calls = meter.create_counter("agent.tool.calls")
tool_fail = meter.create_counter("agent.tool.failures")
workflows = meter.create_counter("agent.workflow.completed")
wf_latency = meter.create_histogram("agent.workflow.latency", unit="ms")
tok_in = meter.create_counter("agent.llm.tokens.input")
tok_out = meter.create_counter("agent.llm.tokens.output")
llm_err = meter.create_counter("agent.llm.errors")
ground_h = meter.create_histogram("agent.eval.groundedness")
rel_h = meter.create_histogram("agent.eval.relevance")
halluc = meter.create_counter("agent.eval.hallucination_flagged")

MODEL = os.getenv("MODEL", "gemini-2.5-flash")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "")  # used when MODEL returns 429/5xx (e.g. 503 high demand)
FAIL_RATE = float(os.getenv("TOOL_FAIL_RATE", "0.05"))
RETRYABLE = {429, 500, 503, 504}
APP = "adk_obs_demo"
INSTRUCTION = "Answer ONLY using passages returned by retrieve_docs. Call it first. If nothing relevant is returned, say you do not know."
_rec = contextvars.ContextVar("rec", default=None)


class ModelUnavailable(Exception):
    """Every configured model returned a retryable error (429/5xx)."""


def retrieve_docs(query: str) -> dict:
    """Search the internal knowledge base and return the most relevant passages.

    Args:
        query: Natural language search query.
    Returns:
        dict with status and a list of passages (id, text, score).
    """
    with tracer.start_as_current_span("tool.retrieve_docs") as sp:
        tool_calls.add(1, {"tool": "retrieve_docs"})
        if random.random() < FAIL_RATE:
            tool_fail.add(1, {"tool": "retrieve_docs"})
            sp.set_status(trace.Status(trace.StatusCode.ERROR, "kb timeout"))
            return {"status": "error", "passages": []}
        hits = core.search(query)
        sp.set_attribute("retrieval.docs", len(hits))
        sp.set_attribute("retrieval.top_score", hits[0]["score"] if hits else 0.0)
        rec = _rec.get()
        if rec is not None:
            rec["contexts"] += [h["text"] for h in hits]
            rec["ids"] += [h["id"] for h in hits]
        return {"status": "ok", "passages": hits}


session_service = InMemorySessionService()
_runners = {}


def runner_for(model: str) -> Runner:
    if model not in _runners:
        agent = LlmAgent(name="sre_helper", model=model, instruction=INSTRUCTION, tools=[retrieve_docs])
        _runners[model] = Runner(agent=agent, app_name=APP, session_service=session_service)
    return _runners[model]


root_agent = runner_for(MODEL).agent
runner = runner_for(MODEL)


async def _attempt(model, question, user_id, rec):
    sid = uuid.uuid4().hex
    await session_service.create_session(app_name=APP, user_id=user_id, session_id=sid)
    msg = types.Content(role="user", parts=[types.Part(text=question)])
    answer, ti, to = "", 0, 0
    async for ev in runner_for(model).run_async(user_id=user_id, session_id=sid, new_message=msg):
        um = getattr(ev, "usage_metadata", None)
        if um:
            ti += um.prompt_token_count or 0
            to += um.candidates_token_count or 0
        if ev.is_final_response() and ev.content and ev.content.parts:
            answer = ev.content.parts[0].text or ""
    return answer, ti, to


async def run(question: str, user_id: str = "demo"):
    rec = {"question": question, "contexts": [], "ids": []}
    _rec.set(rec)
    models = [MODEL] + ([FALLBACK_MODEL] if FALLBACK_MODEL and FALLBACK_MODEL != MODEL else [])
    t0, answer, ti, to, used, last = time.time(), "", 0, 0, None, None
    with tracer.start_as_current_span("agent.workflow") as sp:
        sc = sp.get_span_context()
        trace_id = format(sc.trace_id, "032x")
        for m in models:
            rec["contexts"].clear()
            rec["ids"].clear()
            try:
                answer, ti, to = await _attempt(m, question, user_id, rec)
                used = m
                break
            except genai_errors.APIError as e:
                if e.code not in RETRYABLE:
                    raise
                last = e
                llm_err.add(1, {"model": m, "code": e.code})
                sp.add_event("llm.error", {"model": m, "code": e.code})
        if used is None:
            workflows.add(1, {"status": "error"})
            core.log("llm_unavailable", trace_id=trace_id, question=question, models=models, code=last.code if last else 0)
            raise ModelUnavailable(f"Model unavailable ({last.code if last else 'unknown'}) for: {', '.join(models)}") from last
        sp.set_attribute("llm.model_used", used)
        sp.set_attribute("llm.fallback_used", used != MODEL)
        tok_in.add(ti, {"model": used})
        tok_out.add(to, {"model": used})
        g, r = core.groundedness_proxy(answer, rec["contexts"]), core.relevance_proxy(question, answer)
        flagged = g < 0.8
        ground_h.record(g)
        rel_h.record(r)
        if flagged:
            halluc.add(1)
        for k, v in (("eval.groundedness", g), ("eval.relevance", r), ("eval.hallucination_flagged", flagged)):
            sp.set_attribute(k, v)
        ms = (time.time() - t0) * 1000
        wf_latency.record(ms)
        workflows.add(1, {"status": "success" if rec["contexts"] else "degraded"})
    rec.update(answer=answer, trace_id=trace_id, span_ctx=sc)
    core.log("request", trace_id=trace_id, question=question, groundedness=round(g, 2), flagged=flagged, latency_ms=round(ms), docs=rec["ids"], model=used)
    core.maybe_enqueue(rec)
    return {"answer": answer, "trace_id": trace_id, "groundedness": round(g, 2), "relevance": round(r, 2),
            "flagged": flagged, "latency_ms": round(ms), "sources": rec["ids"], "model": used}
