"""Google ADK agent with a RAG tool, plus the per-request workflow that records metrics and eval scores."""
import contextvars, os, random, time, uuid
from opentelemetry import trace, metrics
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from . import core

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

MODEL = os.getenv("MODEL", "gemini-2.5-flash")
FAIL_RATE = float(os.getenv("TOOL_FAIL_RATE", "0.05"))
_rec = contextvars.ContextVar("rec", default=None)


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


root_agent = LlmAgent(
    name="sre_helper", model=MODEL,
    instruction="Answer ONLY using passages returned by retrieve_docs. Call it first. If nothing relevant is returned, say you do not know.",
    tools=[retrieve_docs])
session_service = InMemorySessionService()
runner = Runner(agent=root_agent, app_name="adk_obs_demo", session_service=session_service)


async def run(question: str, user_id: str = "demo"):
    sid = uuid.uuid4().hex
    await session_service.create_session(app_name="adk_obs_demo", user_id=user_id, session_id=sid)
    rec = {"question": question, "contexts": [], "ids": []}
    _rec.set(rec)
    t0, answer, ti, to = time.time(), "", 0, 0
    with tracer.start_as_current_span("agent.workflow") as sp:
        sc = sp.get_span_context()
        trace_id = format(sc.trace_id, "032x")
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
    core.log("request", trace_id=trace_id, question=question, groundedness=round(g, 2), flagged=flagged, latency_ms=round(ms), docs=rec["ids"])
    core.maybe_enqueue(rec)
    return {"answer": answer, "trace_id": trace_id, "groundedness": round(g, 2), "relevance": round(r, 2),
            "flagged": flagged, "latency_ms": round(ms), "sources": rec["ids"]}
