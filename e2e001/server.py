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
import threading
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
# WO-COM-E2E-002: named journal sessions; mode "nim" (live) or "script" (deterministic responses, no model involved).
SESSION = {"name": "AI_TURNS", "mode": "nim", "responses": [], "i": 0, "seq": 0, "contract": None}
# WO-COM-E2E-003: optional, explicitly labelled test fixture applied on top of the genuine run dataset (--fixture).
FIXTURE = {"name": None}
FIXTURES = {
    # Mixed-status checks: exactly one genuine PASS row (the last checks row) is overridden to FAIL, so that a KPI that
    # counts all checks (31) and a KPI that counts PASS checks (30) are distinguishable. Offers/events are untouched.
    "e2e003_mixed_checks": {"description": "checks: last row overridden PASS->FAIL (test fixture, not a genuine result); 30 PASS + 1 FAIL",
                            "source": "checks", "override_index": -1, "field": "result", "new_value": "FAIL"},
}
LOCK = threading.Lock()
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
    out = {"run": run.name, "verdict": judge["verdict"], "evidence_mode": judge["evidence_mode"], "lean_verdict": lean["verdict"],
            "final": plan["final_line"], "changed_offer": plan["changed_offer"],
            "sources": [{"id": "offers", "data": offer_rows}, {"id": "events", "data": event_rows}, {"id": "price_gates", "data": gate_rows},
                        {"id": "checks", "data": check_rows}, {"id": "summary", "data": summary}]}
    if FIXTURE["name"]:
        fx = FIXTURES[FIXTURE["name"]]
        rows = next(s for s in out["sources"] if s["id"] == fx["source"])["data"]
        row = rows[fx["override_index"]]
        out["fixture"] = {"name": FIXTURE["name"], "description": fx["description"], "overridden_row": dict(row)}
        row[fx["field"]] = fx["new_value"]
        row["detail"] = f"FIXTURE {FIXTURE['name']}: {fx['field']} overridden to {fx['new_value']} (genuine value was PASS)"
    return out


def contract_with_ground_truth(contract):
    """WO-COM-E2E-003: compute a task contract's ground truth directly from the served dataset (independent of AG Studio)."""
    if not contract:
        return None
    ds = run_dataset()
    rows = next(s for s in ds["sources"] if s["id"] == contract["source"])["data"]
    pred = contract["predicate"]
    col = pred["field"].split(".", 1)[1]
    if pred["operator"] != "equals":
        raise ValueError("unsupported predicate operator")
    matching = [r for r in rows if r.get(col) == pred["value"]]
    return {**contract, "ground_truth": {"count": len(matching), "rows_total": len(rows), "computed_by": "server.py contract_with_ground_truth",
                                          "dataset_run": ds["run"], "fixture": (ds.get("fixture") or {}).get("name")}}


def to_chat(req):
    """AgLlmRequest -> OpenAI-compatible chat completion body for NIM."""
    msgs = []
    names = {t["name"] for t in req.get("tools") or []}
    hint = ("\n\nTool-call format: delegate_to takes {\"type\": {\"id\": \"<agent id>\"}, \"instructions\": \"...\"}; "
            "the agent id is one of data, page, planning, widget.") if "delegate_to" in names else ""
    if "execute_query" in names:
        # WO-COM-E2E-002: two argument objects verified valid by AG Studio's own executeQuery (GUIDANCE_EXAMPLES_PROBE.json),
        # deliberately on the events table so they do not contain any task answer.
        hint += ("\n\nexecute_query examples (the value of \"query\" is a JSON OBJECT, never a string; every field is an object "
                 "{\"id\": ...}; aggregations go in measures, never in dimensions):\n"
                 "projection: {\"query\": {\"variant\": \"projection\", \"kind\": \"apiQuery\", \"projection\": [{\"field\": {\"id\": \"events.seq\"}, \"as\": \"seq\"}, "
                 "{\"field\": {\"id\": \"events.kind\"}, \"as\": \"kind\"}], \"filter\": {\"field\": {\"id\": \"events.agent\"}, \"operator\": \"equals\", "
                 "\"value\": \"agent:chair\"}, \"sort\": [{\"field\": {\"id\": \"events.seq\"}, \"direction\": \"asc\"}], \"limit\": {\"count\": 5}}}\n"
                 "aggregation: {\"query\": {\"variant\": \"aggregation\", \"kind\": \"apiQuery\", \"axes\": [{\"dimensions\": [{\"field\": {\"id\": \"events.agent\"}, "
                 "\"as\": \"agent\"}]}], \"measures\": [{\"field\": {\"id\": \"events.seq\", \"aggregation\": \"count\"}, \"as\": \"events\"}, "
                 "{\"field\": {\"id\": \"events.kind\", \"aggregation\": \"countd\"}, \"as\": \"kinds\"}]}}\n"
                 "For a total over the whole table, omit axes. In an aggregation, sort/limit do not select the group with the "
                 "lowest or highest value; to find which row has the lowest/highest value, use a projection query sorted by that field "
                 "with limit, or read all aggregated rows and compare them.")
    if names:
        hint += ("\n\nGrounding: the dashboard data is only reachable through the tools. Never state a number, name or value "
                 "that did not come from a tool result in this conversation. Tool arguments must match each tool's JSON schema exactly.")
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
    body = {"model": SESSION.get("model") or nim.MODEL, "messages": msgs, "max_tokens": 4000, "temperature": 0.3,
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
    transport_retries = []
    # E2E-002 (after final1_T5 ended on NIM HTTP 429): rate-limit / unavailable responses are retried with backoff.
    # Each retry is recorded in meta.transport_retries; the request body is identical on every retry.
    for attempt in range(6):
        try:
            with urllib.request.urlopen(http, timeout=180) as r:
                resp = json.load(r)
            break
        except urllib.error.HTTPError as e:
            detail = e.read()[:400].decode(errors="replace")
            if e.code in (429, 502, 503) and attempt < 5:
                transport_retries.append({"attempt": attempt + 1, "status": e.code, "at": time.time()})
                time.sleep(5 * (attempt + 1))
                continue
            return {"id": "err-" + uuid.uuid4().hex, "createdAt": int(t0 * 1000), "status": "failed", "output": [],
                    "error": {"code": f"NIM_HTTP_{e.code}", "message": detail}}, body, {"transport_retries": transport_retries}
    choice = resp["choices"][0]
    msg = choice["message"]
    nim_turn.last_raw = resp
    out = []
    if msg.get("content") and not msg.get("tool_calls"):
        out.append({"id": "msg_" + uuid.uuid4().hex, "kind": "output", "type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "text", "text": msg["content"], "annotations": []}]})
    normalized = []
    for tc in msg.get("tool_calls") or []:
        # WO-COM-E2E-002: model output is forwarded verbatim. The E2E-001 delegate_to string->object rewrite was removed;
        # schema conformance is now enforced by validation + feedback in the browser adapter (llmAdapter.js).
        args = tc["function"].get("arguments") or "{}"
        out.append({"id": "fc_" + uuid.uuid4().hex, "kind": "output", "type": "function_call", "callId": tc["id"],
                    "name": tc["function"]["name"], "arguments": args, "status": "completed"})
    usage = resp.get("usage") or {}
    res = {"id": resp.get("id") or uuid.uuid4().hex, "createdAt": int(t0 * 1000), "model": resp.get("model"),
           "status": "incomplete" if choice.get("finish_reason") == "length" else "completed", "output": out,
           "usage": {"inputTokens": usage.get("prompt_tokens"), "outputTokens": usage.get("completion_tokens"), "totalTokens": usage.get("total_tokens")}}
    if choice.get("finish_reason") == "length":
        res["incompleteDetails"] = {"reason": "max_output_tokens"}
    return res, body, {"latency_s": round(time.time() - t0, 2), "finish_reason": choice.get("finish_reason"), "normalized_tool_args": normalized,
                       "transport_retries": transport_retries}


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
        if path == "/api/contract":
            return self.send(200, {"contract": contract_with_ground_truth(SESSION.get("contract"))})
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
        if self.path == "/api/llm/session":
            with LOCK:
                SESSION.update({"name": str(req.get("name") or "AI_TURNS"), "mode": req.get("mode", "nim"),
                                "responses": req.get("responses") or [], "i": 0, "seq": 0, "contract": req.get("contract"),
                                "model": req.get("model")})
            return self.send(200, {"session": SESSION["name"], "mode": SESSION["mode"], "scripted": len(SESSION["responses"]), "model": SESSION.get("model") or nim.MODEL,
                                   "contract": contract_with_ground_truth(SESSION["contract"])})
        if self.path == "/api/llm/gate":
            # WO-COM-E2E-003: completion-gate decisions from the browser adapter, journaled next to the session's turns.
            TURNS.mkdir(exist_ok=True)
            with LOCK, (TURNS / f"{SESSION['name']}.gate.jsonl").open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps({"session": SESSION["name"], "at": time.time(), **req}, default=str) + "\n")
            return self.send(200, {"ok": True})
        if self.path == "/api/llm/turn":
            with LOCK:
                SESSION["seq"] += 1
                seq, name, mode = SESSION["seq"], SESSION["name"], SESSION["mode"]
                if mode == "script":
                    if SESSION["i"] < len(SESSION["responses"]):
                        res = dict(SESSION["responses"][SESSION["i"]]); SESSION["i"] += 1
                    else:
                        res = {"id": f"script-exhausted-{seq}", "createdAt": int(time.time() * 1000), "status": "failed", "output": [],
                               "error": {"code": "SCRIPT_EXHAUSTED", "message": "no scripted response left"}}
                    body, meta, raw = None, {"scripted_index": SESSION["i"] - 1}, None
            if mode != "script":
                nim_turn.last_raw = None
                res, body, meta = nim_turn(req)
                raw = nim_turn.last_raw
            TURNS.mkdir(exist_ok=True)
            with LOCK, (TURNS / f"{name}.jsonl").open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps({"session": name, "seq": seq, "mode": mode, "at": time.time(), "model": None if mode == "script" else (body or {}).get("model"),
                                    "tools_offered": [t["name"] for t in req.get("tools") or []], "request": req, "body": body,
                                    "raw_provider_response": raw, "response": res, "meta": meta}, default=str) + "\n")
            return self.send(200, res)
        if self.path == "/api/channel3/search":
            c3 = Channel3(TURNS / "CHANNEL3_RAW")
            resp, meta = c3.search(str(req.get("query", ""))[:200], max_price=min(float(req.get("max_price") or 500), 500.0), limit=10, label="dashboard_search")
            rows = [o for p in resp["products"] for o in offers_of(p, meta)]
            return self.send(200, {"notice": "discovery only; not a purchase", "offers": rows, "raw_response_sha256": meta["response_sha256"]})
        return self.send(404, {"error": "not found"})


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--fixture", choices=sorted(FIXTURES)); a = ap.parse_args()
    FIXTURE["name"] = a.fixture
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(json.dumps({"dashboard": f"http://127.0.0.1:{a.port}/", "fixture": a.fixture}), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
