from . import core
core.setup()  # must run before ADK is imported
import asyncio, html, os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from opentelemetry import metrics
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from . import agent, dt_http

dt_http.install(core)  # optional hybrid: metrics + logs over plain HTTP when DT_HTTP_INGEST=1
feedback = metrics.get_meter("app").create_counter("agent.feedback")
PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>ADK observability demo</title><!--RUM-->
<style>body{font-family:sans-serif;max-width:720px;margin:2rem auto}input{width:70%;padding:6px}pre{background:#f3f5f7;padding:10px;white-space:pre-wrap}</style></head>
<body><h2>ADK agent: observability demo</h2><input id="q" placeholder="Ask about SLOs, OTLP, Cloud Run, GKE..."> <button id="go">Ask</button>
<pre id="out"></pre><div id="fb" style="display:none"><button onclick="fb('up')">Helpful</button> <button onclick="fb('down')">Not helpful</button></div>
<script>let tid="";
document.getElementById("go").onclick=async()=>{const q=document.getElementById("q").value,o=document.getElementById("out");o.textContent="...";
const r=await(await fetch("ask?q="+encodeURIComponent(q))).json();tid=r.trace_id;
o.textContent=r.answer+"\\n\\ngroundedness="+r.groundedness+" latency_ms="+r.latency_ms+" trace="+r.trace_id;document.getElementById("fb").style.display="block";};
async function fb(v){await fetch("feedback?trace_id="+tid+"&rating="+v,{method:"POST"});}</script></body></html>"""


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(core.worker())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/healthz")
def health():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def index():
    rum = os.getenv("DT_RUM_SCRIPT_URL", "")  # script URL from Dynatrace: web app -> JavaScript tag
    tag = f'<script src="{html.escape(rum)}" crossorigin="anonymous"></script>' if rum else "<!-- set DT_RUM_SCRIPT_URL for RUM -->"
    return PAGE.replace("<!--RUM-->", tag)


@app.get("/ask")
async def ask(q: str):
    return await agent.run(q)


@app.post("/feedback")
def post_feedback(trace_id: str, rating: str):
    feedback.add(1, {"rating": "up" if rating == "up" else "down"})
    core.log("feedback", trace_id=trace_id, rating=rating)
    return {"ok": True}
