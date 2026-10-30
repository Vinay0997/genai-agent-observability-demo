import os, random, time
from opentelemetry import trace, metrics
from .evals import groundedness, relevance

tracer = trace.get_tracer("agent")
meter = metrics.get_meter("agent")
llm_latency = meter.create_histogram("agent.llm.latency", unit="ms")
tok_in = meter.create_counter("agent.llm.tokens.input")
tok_out = meter.create_counter("agent.llm.tokens.output")
tool_calls = meter.create_counter("agent.tool.calls")
tool_fail = meter.create_counter("agent.tool.failures")
workflows = meter.create_counter("agent.workflow.completed")
ground_h = meter.create_histogram("agent.eval.groundedness")
rel_h = meter.create_histogram("agent.eval.relevance")
halluc = meter.create_counter("agent.eval.hallucination_flagged")

KB = {"slo": "An SLO is a target for a service level indicator such as p95 latency under 2 seconds or 99.5 percent availability.",
      "otel": "OpenTelemetry exports traces, metrics and logs over OTLP to backends such as Dynatrace.",
      "gke": "On GKE the Dynatrace Operator deploys OneAgent and ActiveGate; on Cloud Run you rely on OTLP export from the app."}


def retrieve(q):
    with tracer.start_as_current_span("tool.retrieve") as sp:
        tool_calls.add(1, {"tool": "retrieve"})
        if random.random() < 0.05:
            tool_fail.add(1, {"tool": "retrieve"})
            sp.set_status(trace.Status(trace.StatusCode.ERROR, "kb timeout"))
            return []
        hits = [v for k, v in KB.items() if k in q.lower()]
        sp.set_attribute("retrieval.docs", len(hits))
        return hits


def call_llm(q, ctx):
    with tracer.start_as_current_span("gen_ai.chat") as sp:
        t0 = time.time()
        model = os.getenv("MODEL", "gemini-2.0-flash")
        sp.set_attribute("gen_ai.request.model", model)
        if os.getenv("GOOGLE_API_KEY"):
            from google import genai
            r = genai.Client().models.generate_content(
                model=model, contents=f"Context: {ctx}\nQuestion: {q}\nAnswer only from context.")
            ans, ti, to = r.text, r.usage_metadata.prompt_token_count, r.usage_metadata.candidates_token_count
        else:  # offline mock; occasionally hallucinates so eval alerts have something to fire on
            time.sleep(random.uniform(0.2, 1.2))
            ans = (" ".join(ctx) if ctx else "") or "Unknown"
            if random.random() < 0.2:
                ans += " It was invented in 1850 by Zorblax Corp."
            ti, to = 120, len(ans.split())
        ms = (time.time() - t0) * 1000
        llm_latency.record(ms, {"model": model})
        tok_in.add(ti, {"model": model})
        tok_out.add(to, {"model": model})
        sp.set_attribute("gen_ai.usage.input_tokens", ti)
        sp.set_attribute("gen_ai.usage.output_tokens", to)
        return ans


def run(question):
    with tracer.start_as_current_span("agent.workflow") as sp:
        ctx = retrieve(question)
        ans = call_llm(question, ctx)
        g, r = groundedness(ans, ctx), relevance(question, ans)
        ground_h.record(g)
        rel_h.record(r)
        flagged = g < 0.8
        if flagged:
            halluc.add(1)
        sp.set_attribute("eval.groundedness", g)
        sp.set_attribute("eval.relevance", r)
        sp.set_attribute("eval.hallucination_flagged", flagged)
        workflows.add(1, {"status": "success" if ctx else "degraded"})
        return {"answer": ans, "groundedness": round(g, 2), "relevance": round(r, 2), "flagged": flagged}
