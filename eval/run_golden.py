"""Golden-set regression and load generator. Run from the repo root:
  python -m eval.run_golden --retrieval-only        # no keys needed
  python -m eval.run_golden                         # full agent run (needs GOOGLE_API_KEY), emits agent.golden.* metrics
  python -m eval.run_golden --load http://URL       # simple load generator against a deployed service
Exits 1 below thresholds so it can gate CI."""
import argparse, asyncio, json, os, random, sys, time

GOLDEN = [
    ("What is an SLO and what is an error budget?", ["slo-basics"]),
    ("How does Dynatrace ingest OpenTelemetry data?", ["otlp-dynatrace"]),
    ("How do I get telemetry from Cloud Run to Dynatrace?", ["cloudrun-observability"]),
    ("How do I monitor an agent on GKE with a collector and the operator?", ["gke-observability"]),
    ("Which spans does Google ADK emit?", ["adk-tracing"]),
    ("How is groundedness and retrieval quality measured?", ["genai-evaluation"]),
    ("How does real user monitoring work?", ["rum"]),
]
ap = argparse.ArgumentParser()
ap.add_argument("--retrieval-only", action="store_true")
ap.add_argument("--load")
ap.add_argument("--min-hit", type=float, default=0.85)
ap.add_argument("--min-grounded", type=float, default=0.6)
a = ap.parse_args()

if a.load:
    import httpx
    while True:
        q = random.choice(GOLDEN + [("tell me about quantum pizza", [])])[0]
        try:
            print(httpx.get(f"{a.load}/ask", params={"q": q}, timeout=60).json())
        except Exception as e:
            print("err", e)
        time.sleep(random.uniform(1, 3))

from app import core
if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
    core.setup("adk-agent-golden-eval")
from opentelemetry import metrics
hits, rrs, gs = [], [], []
if a.retrieval_only:
    for q, exp in GOLDEN:
        h, rr = core.hit_and_rr([d["id"] for d in core.search(q)], exp)
        hits.append(h); rrs.append(rr)
else:
    from app import agent
    async def go():
        for q, exp in GOLDEN:
            r = await agent.run(q)
            h, rr = core.hit_and_rr(r["sources"], exp)
            hits.append(h); rrs.append(rr); gs.append(r["groundedness"])
    asyncio.run(go())
hit, mrr = sum(hits) / len(hits), sum(rrs) / len(rrs)
g = sum(gs) / len(gs) if gs else None
m = metrics.get_meter("golden")
m.create_gauge("agent.golden.hit_rate").set(hit)
m.create_gauge("agent.golden.mrr").set(mrr)
if g is not None:
    m.create_gauge("agent.golden.groundedness").set(g)
print(json.dumps({"n": len(GOLDEN), "hit_rate": round(hit, 3), "mrr": round(mrr, 3), "groundedness": g and round(g, 3)}))
fl = getattr(metrics.get_meter_provider(), "force_flush", None)
if fl:
    fl()
sys.exit(0 if hit >= a.min_hit and (g is None or g >= a.min_grounded) else 1)
