"""LEGION Deal Rescue - local read-only product server.

Serves the merchant UI and the run-linked evidence. It has no write path: no model calls, no PayPal calls,
no mutation of run files. `--mode replay` is the only mode implemented here (live payment needs owner authority).

  python -B -m deal_rescue.server --evidence wo004_evidence --port 8795
"""
import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

WEB = Path(__file__).parent / "web"
EV = None
RUNS = {}  # run_id -> (run_dir, dossier_dir)


def resolve_ref(run_dir, ref):
    """ref = 'FILE#seq' (records carrying seq) or 'FILE@line' (0-based line)."""
    if "#" in ref:
        f, s = ref.split("#", 1)
        key, val = "seq", int(s)
    else:
        f, s = ref.split("@", 1)
        key, val = "line", int(s)
    p = (run_dir / f).resolve()
    if run_dir.resolve() not in p.parents:
        raise ValueError("ref outside run")
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows[val] if key == "line" else next(r for r in rows if r.get("seq") == val)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json", extra=None):
        b = body if isinstance(body, bytes) else json.dumps(body, indent=1).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/index.html"):
                return self.send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path.startswith("/web/"):
                p = (WEB / u.path[5:]).resolve()
                if WEB.resolve() not in p.parents:
                    return self.send(404, {"error": "not found"})
                return self.send(200, p.read_bytes(), mimetypes.guess_type(str(p))[0] or "application/octet-stream")
            if u.path == "/api/runs":
                return self.send(200, [{"run_id": k, "evidence_mode": json.loads((d / "DEAL_DOSSIER.json").read_text())["evidence_mode"]}
                                       for k, (_, d) in RUNS.items()])
            if u.path == "/api/dossier":
                return self.send(200, (RUNS[q["run"]][1] / "DEAL_DOSSIER.json").read_bytes())
            if u.path == "/api/download":
                name = "EXECUTION_PASSPORT.json" if q.get("kind") == "passport" else "DEAL_DOSSIER.json"
                return self.send(200, (RUNS[q["run"]][1] / name).read_bytes(), "application/json",
                                 {"Content-Disposition": f'attachment; filename="{name[:-5]}_{q["run"]}.json"'})
            if u.path == "/api/record":
                return self.send(200, {"run_id": q["run"], "ref": q["ref"], "record": resolve_ref(RUNS[q["run"]][0], q["ref"])})
            if u.path == "/api/context":
                ctx = {}
                for n in ["PAYPAL_AUTHORITY_PREFLIGHT.json", "OLD_HOLDS_RECONCILIATION_READ_ONLY.json", "LICENSE_GATE.json",
                          "COUNTERFACTUAL_A0_A1_A2_A3.json", "NEGATIVE_TESTS.json", "ECONOMIC_CASE.json"]:
                    p = EV / n
                    ctx[n] = json.loads(p.read_text()) if p.exists() else {"status": "Z", "note": "evidence file not produced"}
                return self.send(200, ctx)
            return self.send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001 - surface the error to the UI rather than crash the server
            return self.send(500, {"error": f"{type(e).__name__}: {e}", "status": "EVIDENCE UNAVAILABLE"})


def main():
    global EV
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--port", type=int, default=8795)
    ap.add_argument("--mode", default="replay", choices=["replay"])
    a = ap.parse_args()
    EV = Path(a.evidence).resolve()
    for d in sorted((EV / "dossier").iterdir()):
        dj = json.loads((d / "DEAL_DOSSIER.json").read_text())
        RUNS[dj["run_id"]] = (EV / "runs" / dj["run_dir_name"], d)
    print(json.dumps({"url": f"http://127.0.0.1:{a.port}/", "runs": list(RUNS)}), flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
