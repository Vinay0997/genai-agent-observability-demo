"""Streamlit front end for the same agent. Run from the repo root:  python -m streamlit run app/ui.py
Telemetry setup, the ADK agent and the live-judge worker are started once per process."""
import asyncio, os, sys, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import streamlit as st

st.set_page_config(page_title="ADK observability demo", layout="wide")


@st.cache_resource
def boot():
    from app import core
    core.setup()  # must run before ADK is imported
    from app import agent, dt_http
    dt_http.install(core)  # optional hybrid HTTP ingest when DT_HTTP_INGEST=1
    loop = asyncio.new_event_loop()  # one long-lived loop for the agent and the judge worker
    threading.Thread(target=loop.run_forever, daemon=True).start()
    asyncio.run_coroutine_threadsafe(core.worker(), loop)
    from opentelemetry import metrics
    return core, agent, loop, metrics.get_meter("app").create_counter("agent.feedback")


core, agent, loop, feedback = boot()


def ask(q):
    return asyncio.run_coroutine_threadsafe(agent.run(q), loop).result(timeout=120)


with st.sidebar:
    st.header("Demo controls")
    agent.FAIL_RATE = st.slider("Tool failure rate", 0.0, 1.0, float(agent.FAIL_RATE), 0.05, help="Raise it to break the tool and trigger alerts")
    core.SAMPLE = st.slider("Judge sample rate", 0.0, 1.0, float(core.SAMPLE), 0.1, help="Share of requests judged by Gemini")
    st.caption("Traces and metrics go to Dynatrace via OTLP. Use these sliders for the break-it step.")

st.title("ADK agent: observability demo")
q = st.chat_input("Ask about SLOs, OTLP, Cloud Run, GKE, RUM...")
if q:
    with st.spinner("Running agent..."):
        st.session_state["last"] = {"q": q, **ask(q)}
r = st.session_state.get("last")
if r:
    with st.chat_message("user"):
        st.write(r["q"])
    with st.chat_message("assistant"):
        st.write(r["answer"] or "(no answer)")
        c1, c2, c3 = st.columns(3)
        c1.metric("Groundedness (proxy)", r["groundedness"])
        c2.metric("Latency (ms)", r["latency_ms"])
        c3.metric("Flagged", "yes" if r["flagged"] else "no")
        st.caption(f"sources: {', '.join(r['sources']) or 'none'}  |  trace_id: {r['trace_id']}")
        b1, b2, _ = st.columns([1, 1, 6])
        for col, label, val in ((b1, "Helpful", "up"), (b2, "Not helpful", "down")):
            if col.button(label, key=f"fb_{val}_{r['trace_id']}"):
                feedback.add(1, {"rating": val})
                core.log("feedback", trace_id=r["trace_id"], rating=val)
                st.toast("Feedback recorded")
