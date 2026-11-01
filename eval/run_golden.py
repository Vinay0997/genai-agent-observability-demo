"""Golden-set regression and traffic generator. Run from the repo root:
  python -m eval.run_golden --retrieval-only                       # no keys needed
  python -m eval.run_golden                                        # full agent run in-process (needs GOOGLE_API_KEY)
  python -m eval.run_golden --url https://SERVICE_URL --repeat 20  # traffic against a deployed service (--token or ID_TOKEN env for private Cloud Run)
Exits 1 below thresholds so it can gate CI."""
import argparse, asyncio, json, os, sys, time, urllib.parse, urllib.request

GOLDEN = [
    ("What is an SLO and what is an error budget?", ["slo-basics"]),
    ("How does Dynatrace ingest OpenTelemetry data?", ["otlp-dynatrace"]),
    ("How do I get telemetry from Cloud Run to Dynatrace?", ["cloudrun-observability"]),
    ("How do I monitor an agent on GKE with a collector and the operator?", ["gke-observability"]),
    ("Which spans does Google ADK emit?", ["adk-tracing"]),
    ("How is groundedness and retrieval quality measured?", ["genai-evaluation"]),
    ("How does real user monitoring work?", ["rum"]),
]
EXTRA = "tell me about quantum pizza"  # off-topic question: exercises the low-groundedness path
ap = argparse.ArgumentParser()
ap.add_argument("--retrieval-only", action="store_true")
ap.add_argument("--url")
ap.add_argument("--token", default=os.getenv("ID_TOKEN"))
ap.add_argument("--repeat", type=int, default=1)
ap.add_argument("--sleep", type=float, default=0.5)
ap.add_argument("--min-hit", type=float, default=0.85)
ap.add_argument("--min-grounded", type=float, default=0.6)
a = ap.parse_args()

if a.url:
    for _ in range(a.repeat):
        for q, _exp in GOLDEN + [(EXTRA, [])]:
            req = urllib.request.Request(a.url.rstrip("/") + "/ask?" + urllib.parse.urlencode({"q": q}),
                                         headers={"Authorization": f"Bearer {a.token}"} if a.token else {})
            try:
                print(urllib.request.urlopen(req, timeout=120).read().decode()[:200])
            except Exception as e:
                print("err", e)
            time.sleep(a.sleep)
    sys.exit(0)

from app import core
if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
    core.setup("adk-agent-demo")
from opentelemetry import metrics
hits, rrs, gs = [], [], []
if a.retrieval_only:
    for q, exp in GOLDEN:
        h, rr = core.hit_and_rr([d["id"] for d in core.search(q)], exp)
        hits.append(h); rrs.append(rr)
else:
    from app import agent

    async def go():
        worker = asyncio.create_task(core.worker())  # live judge runs in this process too
        for _ in range(a.repeat):
            for q, exp in GOLDEN:
                r = await agent.run(q)
                h, rr = core.hit_and_rr(r["sources"], exp)
                hits.append(h); rrs.append(rr); gs.append(r["groundedness"])
                await asyncio.sleep(a.sleep)
            if a.repeat > 1:
                await agent.run(EXTRA)
                await asyncio.sleep(a.sleep)
        await asyncio.sleep(5)  # let queued judge calls finish
        worker.cancel()
    asyncio.run(go())
hit, mrr = sum(hits) / len(hits), sum(rrs) / len(rrs)
g = sum(gs) / len(gs) if gs else None
m = metrics.get_meter("golden")
m.create_gauge("agent.golden.hit_rate").set(hit)
m.create_gauge("agent.golden.mrr").set(mrr)
if g is not None:
    m.create_gauge("agent.golden.groundedness").set(g)
print(json.dumps({"n": len(hits), "hit_rate": round(hit, 3), "mrr": round(mrr, 3), "groundedness": g and round(g, 3)}))
fl = getattr(metrics.get_meter_provider(), "force_flush", None)
if fl:
    fl()
sys.exit(0 if hit >= a.min_hit and (g is None or g >= a.min_grounded) else 1)
