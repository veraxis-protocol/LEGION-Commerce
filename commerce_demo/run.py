"""DEMO-006 orchestrator: one attempt, end to end, with a live dashboard at http://127.0.0.1:8770/.

  python -m commerce_demo.run

Phases (single attempt id): verify owner authorization -> collective proposals -> chair selection ->
price shock -> detection/rejection -> autonomous replanning -> [owner presses "Proceed to payment"] ->
fresh qualified Lean decision + sandbox permit -> A04 Gateway reserve + create -> buyer approval in
PayPal Sandbox -> capture (on callback) -> separate-process readback -> simulated fulfillment receipts
-> separate-process Judge -> VCC@1.
"""
import json
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from commerce_adapter.contracts import digest, verify
from commerce_adapter.gateway import Gateway
from commerce_adapter.paypal import PayPalSandbox
from commerce_authority.custody import Store, sign as raw_sign
from commerce_bridge.consolidated import install
from commerce_bridge.qualified import QualifiedSources, QUALIFIED, QUALIFIED_DECIDE_SHA256
from . import collective, sandbox_admission as S
from .events import Log
from .mission import ROOT, current_run, load_runtime_keys

UI_PORT, CALLBACK_PORT = 8770, 8765
STATE = {"phase": "STARTING", "proceed": threading.Event(), "approval_url": None, "effect": None}


def write(run, name, value):
    (run / name).write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")


def phase(log, name, **data):
    STATE["phase"] = name
    log["events"].emit("PHASE", "agent:coordinator", phase=name, **data)


def ui_server(run):
    page = (Path(__file__).with_name("dashboard.html")).read_bytes()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _send(self, code, body, ctype="application/json"):
            self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                return self._send(200, page, "text/html; charset=utf-8")
            if self.path == "/api/state":
                files = {}
                for n in ("DEMO_MISSION.json", "PROPOSALS.json", "PRICE_SHOCK.json", "LEAN_AUTHORIZATION.json", "PAYPAL_NATIVE_READBACK.json",
                          "BUDGET_LEDGER.json", "JUDGE_VERDICT.json", "VCC_AT_1.json", "FULFILLMENT_RECEIPTS_SIMULATED.json", "AGENT_REGISTRY.json"):
                    p = run / n
                    if p.exists(): files[n] = json.loads(p.read_text(encoding="utf-8"))
                ev = run / "END_TO_END_EVENTS.jsonl"
                events = [json.loads(l) for l in ev.read_text(encoding="utf-8").splitlines()] if ev.exists() else []
                body = {"phase": STATE["phase"], "approval_url": STATE["approval_url"], "effect": STATE["effect"], "events": events[-400:], "files": files}
                return self._send(200, json.dumps(body, default=str).encode())
            self._send(404, b"{}")

        def do_POST(self):
            if self.path == "/api/proceed" and self.headers.get("Host") == f"127.0.0.1:{UI_PORT}":
                STATE["proceed"].set(); return self._send(200, b'{"ok":true}')
            self._send(404, b"{}")
    srv = ThreadingHTTPServer(("127.0.0.1", UI_PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    run = current_run()
    m_env = json.loads((run / "MISSION_AUTHORIZED.json").read_text())
    c_env = json.loads((run / "CONTROL_AUTHORIZED.json").read_text())
    owner = Store().active("control")
    write(run, "OWNER_CONTROL_PUBLIC.json", {"key_id": owner["key_id"], "public_key": owner["public_key"], "custodian": owner["custodian"]})
    mission = verify(m_env, owner["public_key"], S.MISSION_DOMAIN)          # the owner's authorization is verified first
    attempt = mission["mission_id"] + "-ATTEMPT-1"
    log = {"events": Log(run / "END_TO_END_EVENTS.jsonl", attempt), "activity": Log(run / "AGENT_ACTIVITY.jsonl", attempt)}
    ui_server(run)
    write(run, "DEMO_MISSION.json", {"authorized_envelope": m_env, "control_envelope": c_env, "verified_with_owner_control_key": owner["key_id"],
                                     "mission_hash": digest(S.MISSION_DOMAIN, mission), "attempt_id": attempt})
    phase(log, "MISSION_AUTHORIZED_VERIFIED", mission_id=mission["mission_id"], cap_cents=mission["cap_cents"], owner_key=owner["key_id"],
          dashboard=f"http://127.0.0.1:{UI_PORT}/")
    print(json.dumps({"dashboard": f"http://127.0.0.1:{UI_PORT}/", "attempt": attempt}), flush=True)
    sources = QualifiedSources(ROOT / "runtime_sources", immutable=True); install(sources, hot_standby_seconds=60.0)
    try:
        # ---------------- WP1/WP2: collective
        phase(log, "COLLECTIVE_PLANNING")
        trace = collective.run(log, mission, run)
        final = trace["final"]
        write(run, "PROPOSALS.json", {"simulated_supplier_data": True, "initial_round": trace["plans_initial"], "selected_initial": trace["selected_initial"],
                                      "chair_initial": trace["chair_initial"], "replan_round": trace["plans_replan"], "chair_replan": trace["chair_replan"],
                                      "final_plan_id": final["plan_id"]})
        write(run, "PRICE_SHOCK.json", {**trace["price_shock"], "recheck": trace["recheck_after_shock"], "replanned": trace["replanned"],
                                        "feed_v1": trace["feeds"]["v1"], "feed_v2": trace["feeds"]["v2"]})
        with (run / "REPLAN_TRACE.jsonl").open("w", encoding="utf-8", newline="\n") as f:
            for l in (run / "END_TO_END_EVENTS.jsonl").read_text(encoding="utf-8").splitlines():
                if json.loads(l)["kind"] in ("PLAN_SELECTED", "PRICE_SHOCK", "PLAN_RECHECKED", "PLAN_REJECTED", "PLAN_PROPOSED", "FINAL_PLAN"):
                    f.write(l + "\n")
        lines, total = final["evaluation"]["normalized_lines"], final["evaluation"]["total_cents"]
        # ---------------- WP3: wait for the owner, then a fresh Lean decision
        phase(log, "AWAITING_OWNER_PROCEED", final_plan=final["plan_id"], total_cents=total)
        print(json.dumps({"awaiting": "press 'Proceed to payment' on the dashboard"}), flush=True)
        STATE["proceed"].wait()
        phase(log, "LEAN_AUTHORIZATION")
        runtime = load_runtime_keys(run.name)
        adm = S.SandboxDemoAdmission(owner["public_key"], mission["runtime_public_keys"], sources)
        p_env, decision, observation = S.issue_permit(adm, m_env, runtime, lines, total, sources)
        write(run, "LEAN_AUTHORIZATION.json", {"profile": "sandbox-demo-v1", "verdict": decision["lean_verdict"], "decision": decision,
              "permit_envelope": p_env, "core_commit": QUALIFIED, "decider_sha256": QUALIFIED_DECIDE_SHA256,
              "admission_hot_path_commit": "ca897f7badeea4f2b66a52bc684df18008bc4f4d", "action": observation["action"],
              "controls_exercised": ["owner Control-Key mission + control signatures", "control active/epoch/expiry/revocation", "permit signature/expiry",
                                     "amount<=cap, payee, actor, currency", "Core observation signature/binding/freshness/state/chronology/prerequisites",
                                     "qualified Core ACT + Lean decider T for the exact action (re-evaluated at reserve, create and capture)",
                                     "A04 durable budget, idempotency, revocation ledger, provider safety", "buyer approval in PayPal"],
              "production_controls_not_in_profile": ["independent A03 acceptance + installation manifest", "A01/A02 upstream acceptance",
                                                     "separately staffed admission issuer", "CAGE and VEIP authorizations"]})
        log["events"].emit("LEAN_DECISION", "qualified-core", verdict=decision["lean_verdict"], directive=decision["directive"]["directive"],
                           amount_cents=total, merchant=mission["merchant_ids"][0], permit_id=p_env["payload"]["permit_id"])
        # ---------------- WP4: PayPal Sandbox
        phase(log, "PAYMENT_CREATE")
        g = Gateway(run / "ledger.db", adm, PayPalSandbox(read_only=False))
        g.apply_control(mission["mission_id"], c_env)
        eid = g.reserve(m_env, p_env); STATE["effect"] = eid
        state = g.create(eid, CALLBACK_PORT)
        st = g.status(eid); STATE["approval_url"] = st.get("approval_url")
        log["events"].emit("ORDER_CREATED", "a04-gateway", effect_id=eid, state=state, order_id=st.get("order_id"), approval_url=st.get("approval_url"))
        phase(log, "AWAITING_BUYER_APPROVAL", approval_url=st.get("approval_url"), approve_within_seconds=S.MAX_AGE - 30)
        print(json.dumps({"approval_url": st.get("approval_url")}), flush=True)
        from commerce_adapter.approval import make_server
        cb = make_server(g, eid, CALLBACK_PORT); threading.Thread(target=cb.serve_forever, daemon=True).start()
        deadline = time.time() + 900
        while time.time() < deadline:
            s = g.status(eid)["state"]
            if s in ("CAPTURE_CONFIRMED", "FAILED_RELEASED", "RECONCILIATION_PENDING"): break
            time.sleep(2)
        cb.shutdown()
        final_state = g.status(eid)["state"]
        log["events"].emit("PAYMENT_STATE", "a04-gateway", effect_id=eid, state=final_state)
        con = sqlite3.connect(run / "ledger.db"); con.row_factory = sqlite3.Row
        effects = [{k: r[k] for k in ("id", "mission_id", "obligation_id", "permit_id", "amount", "held", "state", "order_id", "capture_id", "capture_started")}
                   for r in con.execute("SELECT * FROM effects")]
        evs = [{"seq": r["seq"], "at": r["at"], "effect_id": r["effect_id"], "kind": r["kind"], "data": json.loads(r["data"])} for r in con.execute("SELECT * FROM events ORDER BY seq")]
        con.close()
        write(run, "BUDGET_LEDGER.json", {"cap_cents": mission["cap_cents"], "effects": effects, "ledger_events": evs,
                                          "held_total_cents": sum(e["amount"] for e in effects if e["held"]),
                                          "network_posts": sum(1 for e in evs if e["kind"] == "NETWORK" and e["data"].get("method") == "POST")})
        if final_state != "CAPTURE_CONFIRMED":
            phase(log, "PAYMENT_NOT_CONFIRMED", state=final_state)
        else:
            phase(log, "INDEPENDENT_READBACK")
            subprocess.run([sys.executable, "-B", "-m", "commerce_demo.readback", str(run), eid], cwd=ROOT, check=True)
            rb = json.loads((run / "PAYPAL_NATIVE_READBACK.json").read_text())
            log["events"].emit("PROVIDER_READBACK", "independent-readback", order_id=rb["order"]["id"], capture_id=rb["capture"]["id"],
                               status=rb["capture"]["status"], amount=rb["capture"]["amount"])
            phase(log, "FULFILLMENT_SIMULATED")
            receipts = [raw_sign(runtime["fulfillment_simulator"], "commerce.demo.fulfillment-receipt.v1",
                                 {"schema": "commerce.demo.fulfillment-receipt.v1", "simulated": True, "service_id": l["service_id"],
                                  "supplier_id": l["supplier_id"], "quote_id": l["quote_id"], "price_cents": l["price_cents"],
                                  "paypal_capture_id": rb["capture"]["id"], "paypal_order_id": rb["order"]["id"],
                                  "status": "FULFILLED_SIMULATED", "issued_at": int(time.time())}) for l in lines]
            write(run, "FULFILLMENT_RECEIPTS_SIMULATED.json", {"notice": "SIMULATED supplier fulfillment - not real-world delivery", "receipts": receipts})
            log["events"].emit("FULFILLMENT_RECEIPTS", "fulfillment-simulator", count=len(receipts), simulated=True)
        phase(log, "JUDGE")
        subprocess.run([sys.executable, "-B", "-m", "commerce_demo.judge", str(run)], cwd=ROOT)
        v = json.loads((run / "VCC_AT_1.json").read_text()) if (run / "VCC_AT_1.json").exists() else {}
        phase(log, "COMPLETE", vcc_at_1=v.get("vcc_at_1"), verdict=v.get("verdict"))
        print(json.dumps({"done": True, "vcc_at_1": v.get("vcc_at_1"), "verdict": v.get("verdict")}), flush=True)
        time.sleep(600)   # keep the dashboard up for viewing
    finally:
        sources.close()


if __name__ == "__main__":
    main()
