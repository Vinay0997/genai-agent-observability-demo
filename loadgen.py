import httpx, random, time, sys

base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
qs = ["what is an slo", "how does otel export data", "gke vs cloud run dynatrace", "tell me about quantum pizza"]
while True:
    try:
        print(httpx.get(f"{base}/ask", params={"q": random.choice(qs)}, timeout=60).json())
    except Exception as e:
        print("err", e)
    time.sleep(random.uniform(1, 3))
