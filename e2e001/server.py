"""WO-COM-E2E-001 backend for the AG Studio dashboard (127.0.0.1 only).

  python -B -m e2e001.server [--port 8790]

* GET  /                      the built AG Studio dashboard (web/dist)
* GET  /api/run               the latest E2E run as AG Studio data sources (offers, events, gates, verdicts, negatives)
* GET  /api/ag-license        the AG Studio licence key, read from the Desktop file at request time. An AG licence key is a
                              client-side licence by design; it is never written into the bundle or the repository.
* POST /api/llm/turn          AG Studio AgLlmAdapter turn -> NVIDIA NIM chat completions (the LEGION LLM interface). The NIM
                              key never leaves this process. Every turn is journaled to e2e001/ai_turns/AI_TURNS.jsonl.
* POST /api/channel3/search   read-only Channel3 discovery through the backend (the Channel3 key never leaves this process).

No endpoint can admit, reserve, create or capture a payment.
"""
import argparse
import json
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from commerce_demo import nim

from .channel3 import Channel3, offers_of

HERE = Path(__file__).resolve().parent
DIST = HERE / "web" / "dist"
RUNS = HERE / "runs"
TURNS = HERE / "ai_turns"
AG_KEY_FILE = r"C:\Users\Administrator\Desktop\AG_STUDIO_LICENSE_KEY.txt"


def latest_run():
    runs = sorted(p for p in RUNS.glob("run-*") if (p / "JUDGE_VERDICT.json").exists())
    return runs[-1] if runs else None


def rd(p):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def run_dataset():
    run = latest_run()
    if run is None:
        return {"run": None, "sources": []}
    offers = rd(run / "OFFERS.json")["offers"]
    plan = rd(run / "PLAN_TRACE.json")
    events = [json.loads(l) for l in (run / "E2E_EVENTS.jsonl").read_text(encoding="utf-8").splitlines()]
    judge = rd(run / "JUDGE_VERDICT.json")
    neg = rd(run / "NEGATIVES.json") or {"cases": {}}
    jneg = rd(run / "JUDGE_NEGATIVES.json") or {"cases": {}}
    lean = rd(run / "LEAN_AUTHORIZATION.json")
    rejected, final = plan["selection_initial"]["offer_key"], plan["final_line"]["offer_key"]
    offer_rows = [{"offer_key": o["offer_key"], "title": o["title"], "brand": o["brand"] or "", "seller": o["seller"], "source": o["source"],
                   "price": o["price"], "currency": o["currency"], "availability": o["availability"], "rating": o["rating"] or 0,
                   "found_by": o["found_by"],
                   "status": "FINAL (admitted)" if o["offer_key"] == final else "REJECTED (changed offer)" if o["offer_key"] == rejected else "candidate"}
                  for o in offers]
    event_rows = [{"seq": e["seq"], "kind": e["kind"], "agent": e["agent"] or "", "at": time.strftime("%H:%M:%S", time.gmtime(e["at"])),
                   "detail": json.dumps(e["data"], default=str)[:240]} for e in events]
    gate_rows = [{"seq": e["seq"], "plan_price_cents": e["data"]["plan_price_cents"], "live_price_cents": e["data"]["live_price_cents"],
                  "ok": "PASS" if e["data"]["ok"] else "DENY"} for e in events if e["kind"] == "PRICE_GATE"]
    check_rows = [{"suite": "Judge", "check": c["check"], "result": "PASS" if c["pass"] else "FAIL", "detail": c["detail"][:200]} for c in judge["checks"]]
    check_rows += [{"suite": "Transaction negatives", "check": k, "result": v["result"],
                    "detail": str(v.get("denied") or v.get("state") or v.get("states") or v.get("error"))[:200]} for k, v in neg["cases"].items()]
    check_rows += [{"suite": "Judge negatives (must FAIL)", "check": k, "result": "PASS" if v["pass"] else "FAIL",
                    "detail": f"judge verdict {v['verdict']}"} for k, v in jneg["cases"].items()]
    summary = [{"metric": "Budget cap (USD)", "value": 500.0}, {"metric": "Final price (USD)", "value": plan["final_line"]["price_cents"] / 100},
               {"metric": "Genuine Channel3 offers", "value": len(offers)}, {"metric": "Live price gates", "value": len(gate_rows)},
               {"metric": "Captures (MECHANISM_ONLY)", "value": sum(1 for e in events if e["kind"] == "PAYMENT_STATE")}]
    return {"run": run.name, "verdict": judge["verdict"], "evidence_mode": judge["evidence_mode"], "lean_verdict": lean["verdict"],
            "final": plan["final_line"], "changed_offer": plan["changed_offer"],
            "sources": [{"id": "offers", "data": offer_rows}, {"id": "events", "data": event_rows}, {"id": "price_gates", "data": gate_rows},
                        {"id": "checks", "data": check_rows}, {"id": "summary", "data": summary}]}


def to_chat(req):
    """AgLlmRequest -> OpenAI-compatible chat completion body for NIM."""
    msgs = []
    names = {t["name"] for t in req.get("tools") or []}
    hint = ("\n\nTool-call format: delegate_to takes {\"type\": {\"id\": \"<agent id>\"}, \"instructions\": \"...\"}; "
            "the agent id is one of data, page, planning, widget.") if "delegate_to" in names else ""
    if req.get("instructions") or hint:
        msgs.append({"role": "system", "content": (req.get("instructions") or "") + hint})
    for it in req.get("input", []):
        t = it.get("type")
        if t == "message":
            text = "".join(c.get("text", "") for c in it.get("content", []) if c.get("type") == "text")
            role = it.get("role", "user")
            msgs.append({"role": "assistant" if role == "assistant" else role, "content": text})
        elif t == "function_call":
            call = {"id": it["callId"], "type": "function", "function": {"name": it["name"], "arguments": it.get("arguments") or "{}"}}
            if msgs and msgs[-1]["role"] == "assistant" and msgs[-1].get("tool_calls") is not None and not msgs[-1].get("content"):
                msgs[-1]["tool_calls"].append(call)
            else:
                msgs.append({"role": "assistant", "content": None, "tool_calls": [call]})
        elif t == "function_call_output":
            msgs.append({"role": "tool", "tool_call_id": it["callId"], "content": it.get("output", "")})
    body = {"model": nim.MODEL, "messages": msgs, "max_tokens": 4000, "temperature": 0.3,
            "chat_template_kwargs": {"enable_thinking": False}}
    tools = req.get("tools") or []
    if tools:
        body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                           "parameters": t.get("parameters") or {"type": "object", "properties": {}}}} for t in tools]
        tc = req.get("toolChoice")
        if isinstance(tc, dict):
            body["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}
        elif tc in ("auto", "none", "required"):
            body["tool_choice"] = tc
    rf = req.get("responseFormat") or {}
    if rf.get("type") == "json":
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": rf.get("name", "answer"), "schema": rf["schema"]}}
    return body


def nim_turn(req):
    body = to_chat(req)
    key = open(nim.KEY_FILE, encoding="utf-8").read().strip()
    http = urllib.request.Request(nim.ENDPOINT, data=json.dumps(body).encode(),
                                  headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    key = None
    t0 = time.time()
    try:
        with urllib.request.urlopen(http, timeout=180) as r:
            resp = json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read()[:400].decode(errors="replace")
        return {"id": "err-" + uuid.uuid4().hex, "createdAt": int(t0 * 1000), "status": "failed", "output": [],
                "error": {"code": f"NIM_HTTP_{e.code}", "message": detail}}, body, None
    choice = resp["choices"][0]
    msg = choice["message"]
    out = []
    if msg.get("content") and not msg.get("tool_calls"):
        out.append({"id": "msg_" + uuid.uuid4().hex, "kind": "output", "type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "text", "text": msg["content"], "annotations": []}]})
    normalized = []
    for tc in msg.get("tool_calls") or []:
        args = tc["function"].get("arguments") or "{}"
        if tc["function"]["name"] == "delegate_to":
            # Adapter normalisation (journaled): the model often sends type as a bare agent id string, while AG Studio's
            # schema requires {"id": ...}. Only that exact shape is rewritten; anything else is passed through unchanged.
            try:
                a = json.loads(args)
                if isinstance(a.get("type"), str):
                    normalized.append({"call": tc["id"], "raw": args})
                    a["type"] = {"id": a["type"]}
                    args = json.dumps(a)
            except ValueError:
                pass
        out.append({"id": "fc_" + uuid.uuid4().hex, "kind": "output", "type": "function_call", "callId": tc["id"],
                    "name": tc["function"]["name"], "arguments": args, "status": "completed"})
    usage = resp.get("usage") or {}
    res = {"id": resp.get("id") or uuid.uuid4().hex, "createdAt": int(t0 * 1000), "model": resp.get("model"),
           "status": "incomplete" if choice.get("finish_reason") == "length" else "completed", "output": out,
           "usage": {"inputTokens": usage.get("prompt_tokens"), "outputTokens": usage.get("completion_tokens"), "totalTokens": usage.get("total_tokens")}}
    if choice.get("finish_reason") == "length":
        res["incompleteDetails"] = {"reason": "max_output_tokens"}
    return res, body, {"latency_s": round(time.time() - t0, 2), "finish_reason": choice.get("finish_reason"), "normalized_tool_args": normalized}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def local(self):
        return self.client_address[0] == "127.0.0.1" and (self.headers.get("Host") or "").split(":")[0] in ("127.0.0.1", "localhost")

    def send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, default=str).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if not self.local():
            return self.send(403, {"error": "local only"})
        path = self.path.split("?")[0]
        if path == "/api/run":
            return self.send(200, run_dataset())
        if path == "/api/ag-license":
            return self.send(200, {"licenseKey": open(AG_KEY_FILE, encoding="utf-8").read().strip()})
        f = (DIST / ("index.html" if path == "/" else path.lstrip("/"))).resolve()
        if f.is_file() and f.is_relative_to(DIST.resolve()):
            return self.send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        return self.send(404, {"error": "not found"})

    def do_POST(self):
        if not self.local():
            return self.send(403, {"error": "local only"})
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/api/llm/turn":
            res, body, meta = nim_turn(req)
            TURNS.mkdir(exist_ok=True)
            with (TURNS / "AI_TURNS.jsonl").open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps({"at": time.time(), "model": nim.MODEL, "tools_offered": [t["function"]["name"] for t in body.get("tools", [])],
                                    "messages": body["messages"], "response": res, "meta": meta}, default=str) + "\n")
            return self.send(200, res)
        if self.path == "/api/channel3/search":
            c3 = Channel3(TURNS / "CHANNEL3_RAW")
            resp, meta = c3.search(str(req.get("query", ""))[:200], max_price=min(float(req.get("max_price") or 500), 500.0), limit=10, label="dashboard_search")
            rows = [o for p in resp["products"] for o in offers_of(p, meta)]
            return self.send(200, {"notice": "discovery only; not a purchase", "offers": rows, "raw_response_sha256": meta["response_sha256"]})
        return self.send(404, {"error": "not found"})


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8790); a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(json.dumps({"dashboard": f"http://127.0.0.1:{a.port}/"}), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
