from . import telemetry
telemetry.setup()  # must run before ADK import
from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from . import agent

app = FastAPI()
FastAPIInstrumentor.instrument_app(app)


@app.get("/healthz")
def health():
    return {"ok": True}


@app.get("/ask")
async def ask(q: str):
    return await agent.run(q)
