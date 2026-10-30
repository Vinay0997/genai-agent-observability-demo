from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from . import telemetry
telemetry.setup()
from . import agent

app = FastAPI()
FastAPIInstrumentor.instrument_app(app)


@app.get("/healthz")
def health():
    return {"ok": True}


@app.get("/ask")
def ask(q: str):
    return agent.run(q)
