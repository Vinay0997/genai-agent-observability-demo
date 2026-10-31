"""Hybrid mode: ship metrics and logs to Dynatrace over plain HTTP (Metrics API v2 + Log ingest API v2),
alongside the OpenTelemetry traces. Opt in with DT_HTTP_INGEST=1.
Credentials: DT_TENANT + DT_API_TOKEN, or parsed from OTEL_EXPORTER_OTLP_ENDPOINT / _HEADERS when they point at Dynatrace.
Token scopes: metrics.ingest and logs.ingest. Sending is done on a background thread and never raises into the request path."""
import json, os, queue, re, sys, threading, time, urllib.request


def _creds():
    base, token = os.getenv("DT_BASE_URL"), os.getenv("DT_API_TOKEN")
    tenant = os.getenv("DT_TENANT")
    if tenant and not base:
        base = f"https://{tenant}.live.dynatrace.com"
    if not (base and token):
        m = re.match(r"(https://[^/]+\.live\.dynatrace\.com)", os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""))
        t = re.search(r"Api-Token(?:\s|%20)+(\S+)", os.getenv("OTEL_EXPORTER_OTLP_HEADERS", ""))
        if m and t:
            base, token = m.group(1), t.group(1)
    return base, token


class _Sender(threading.Thread):
    def __init__(self, base, token):
        super().__init__(daemon=True)
        self.base, self.token, self.q, self.warned = base.rstrip("/"), token, queue.Queue(maxsize=5000), False

    def put(self, kind, item):
        try:
            self.q.put_nowait((kind, item))
        except queue.Full:
            pass

    def _post(self, path, body, ctype):
        req = urllib.request.Request(self.base + path, data=body, method="POST",
                                     headers={"Authorization": f"Api-Token {self.token}", "Content-Type": ctype})
        try:
            urllib.request.urlopen(req, timeout=5).read()
        except Exception as e:
            if not self.warned:
                self.warned = True
                print(f"[dt_http] ingest error on {path}: {e}", file=sys.stderr, flush=True)

    def run(self):
        while True:
            batch = [self.q.get()]
            t_end = time.time() + 2
            while len(batch) < 100 and time.time() < t_end:
                try:
                    batch.append(self.q.get(timeout=0.5))
                except queue.Empty:
                    pass
            lines = [i for k, i in batch if k == "metric"]
            logs = [i for k, i in batch if k == "log"]
            if lines:
                self._post("/api/v2/metrics/ingest", "\n".join(lines).encode(), "text/plain; charset=utf-8")
            if logs:
                self._post("/api/v2/logs/ingest", json.dumps(logs).encode(), "application/json; charset=utf-8")


def _ship(s, env, event, kw):
    dim = f"env={env}"
    s.put("log", {"content": f"{event} " + json.dumps({k: v for k, v in kw.items() if k != 'logging.googleapis.com/trace'}),
                  "severity": "error" if event.endswith("error") else "info", "event": event,
                  "service.name": "adk-agent-demo", **({"trace_id": kw["trace_id"]} if kw.get("trace_id") else {})})
    if event == "request":
        s.put("metric", f"agent.http.groundedness,{dim} gauge,{kw['groundedness']}")
        s.put("metric", f"agent.http.latency_ms,{dim} gauge,{kw['latency_ms']}")
        if kw.get("flagged"):
            s.put("metric", f"agent.http.flagged,{dim} count,delta=1")
    elif event == "judge_result":
        s.put("metric", f"agent.http.judge.groundedness,{dim} gauge,{kw['groundedness']}")
        s.put("metric", f"agent.http.judge.relevance,{dim} gauge,{kw['relevance']}")
        if kw.get("hallucination"):
            s.put("metric", f"agent.http.judge.hallucination,{dim} count,delta=1")
    elif event == "judge_error":
        s.put("metric", f"agent.http.judge.errors,{dim} count,delta=1")
    elif event == "feedback":
        s.put("metric", f"agent.http.feedback,{dim},rating={'up' if kw.get('rating') == 'up' else 'down'} count,delta=1")


def install(core):
    """Wrap core.log so every structured event is also sent to Dynatrace over HTTP."""
    if os.getenv("DT_HTTP_INGEST") != "1":
        return False
    base, token = _creds()
    if not (base and token):
        print("[dt_http] DT_HTTP_INGEST=1 but no Dynatrace credentials found; skipping", file=sys.stderr, flush=True)
        return False
    s = _Sender(base, token)
    s.start()
    env, orig = os.getenv("ENV", "demo"), core.log

    def wrapped(event, **kw):
        orig(event, **kw)
        try:
            _ship(s, env, event, kw)
        except Exception:
            pass

    core.log = wrapped
    return True
