"""FORMAL-003 trace conformance of the Gateway's external-effect lifecycle to the Lean step relation `gstep`
(LegionAbstraction.Refinement, RF3) — including provider uncertainty.

Scenarios run the qualified sandbox admission (real legion_decide) against a NON-mock provider stub (the Gateway
enforces the Lean attestation) whose replies can be lost, pending, declined or erroring. Each effect's durable event
journal (ledger.db) is then checked:
  C1  at most one create POST and at most one capture POST per effect id (RF3a/RF3b), whatever the provider outcome;
  C2  every POST is immediately preceded, in the effect's journal, by a LEAN_ATTESTED event for its exact intent
      (dispatch_needs_lean), and the attested digest equals digest(intent) of the effect;
  C3  a capture POST only after CAPTURE_INTENT (capture_started), and only from an APPROVED readback;
  C4  CAPTURE_CONFIRMED only through CAPTURE_READBACK (provider readback), never from a POST body;
  C5  the event sequence is accepted by the executable mirror of `gstep` (create from RESERVED only, capture from
      APPROVED with capture_started unset, readback never returns to RESERVED, nor to APPROVED once capture started).
The same checks run on the recorded LMF-007 PayPal Sandbox ledger (pre-closure: LEAN_ATTESTED did not exist yet, so
C2 is reported as NOT_LOGGED there, not as passed).

Usage: python -m commerce_demo.formal003_conformance <out.json> [<recorded ledger.db> ...]
"""
import base64
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from commerce_adapter.contracts import Denied, digest
from commerce_adapter.gateway import Gateway
from commerce_authority.custody import sign as raw_sign
from tests.helpers import FakePayPal

ROOT = Path(__file__).resolve().parent.parent


class ExternalStub(FakePayPal):
    evidence_mode = "PAYPAL_SANDBOX_STUB_NO_NETWORK"


def gstep(g, ev):
    """Executable mirror of LegionAbstraction.gstep (states as strings)."""
    st, started, creates, captures = g
    kind, arg = ev
    if kind == "dispatchCreate":
        return ("CREATE_IN_FLIGHT", started, creates + 1, captures) if st == "RESERVED" and arg else None
    if kind == "dispatchCapture":
        return ("CAPTURE_IN_FLIGHT", True, creates, captures + 1) if st == "APPROVED" and not started and arg else None
    if kind == "readback":
        if arg == "RESERVED" or (started and arg == "APPROVED"):
            return None
        return (arg, started, creates, captures)
    raise ValueError(kind)


STATE_OF = {"ORDER_CREATED": "APPROVAL_PENDING", "CREATE_UNCERTAIN": "RECONCILIATION_PENDING",
            "CAPTURE_READBACK": "CAPTURE_CONFIRMED", "CAPTURE_UNCERTAIN": "RECONCILIATION_PENDING",
            "CAPTURE_DECLINED_READBACK": "FAILED_HELD"}


def check_effect(effect, events, attestation_logged=True):
    evs = [e for e in events if e["effect_id"] == effect["id"]]
    intent_digest = digest("commerce.intent.v1", json.loads(effect["intent"])) if effect.get("intent") else None
    out = {"effect_id": effect["id"], "final_state": effect["state"], "violations": [], "trace": []}
    v = out["violations"]
    posts = [(i, e) for i, e in enumerate(evs) if e["kind"] in ("NETWORK", "NETWORK_ERROR") and e["data"].get("method") == "POST"]

    def op(i, e):
        # NETWORK carries the operation; NETWORK_ERROR does not: use the dispatch intent journaled before it
        if e["data"].get("operation"):
            return e["data"]["operation"]
        intent = next((x["kind"] for x in reversed(evs[:i]) if x["kind"] in ("CREATE_INTENT", "CAPTURE_INTENT")), None)
        return "capture" if intent == "CAPTURE_INTENT" else "orders"
    creates = [(i, e) for i, e in posts if op(i, e) == "orders"]
    captures = [(i, e) for i, e in posts if op(i, e) == "capture"]
    if len(creates) > 1 or len(captures) > 1:
        v.append("C1 more than one create or capture POST")
    for i, e in posts:
        prev = evs[i - 1] if i > 0 else None
        if attestation_logged:
            if not prev or prev["kind"] != "LEAN_ATTESTED" or prev["data"].get("action_digest") != intent_digest:
                v.append(f"C2 POST at seq {e['seq']} not immediately preceded by LEAN_ATTESTED for this intent")
    if not attestation_logged and posts:
        out["c2"] = "NOT_LOGGED (pre-closure ledger)"
    for i, e in captures:
        if not any(x["kind"] == "CAPTURE_INTENT" for x in evs[:i]):
            v.append("C3 capture POST without CAPTURE_INTENT")
        rb = [x for x in evs[:i] if x["kind"] == "ORDER_READBACK"]
        if not rb or rb[-1]["data"].get("provider_status") != "APPROVED":
            v.append("C3 capture POST not from an APPROVED readback")
    if effect["state"] == "CAPTURE_CONFIRMED" and not any(x["kind"] == "CAPTURE_READBACK" for x in evs):
        v.append("C4 CAPTURE_CONFIRMED without provider readback")
    # C5: replay through the gstep mirror
    g = ("RESERVED", False, 0, 0)
    for i, e in enumerate(evs):
        ev = None
        if (i, e) in creates:
            ev = ("dispatchCreate", attestation_logged is False or (i > 0 and evs[i - 1]["kind"] == "LEAN_ATTESTED"))
        elif (i, e) in captures:
            ev = ("dispatchCapture", attestation_logged is False or (i > 0 and evs[i - 1]["kind"] == "LEAN_ATTESTED"))
        elif e["kind"] == "ORDER_READBACK":
            ps = e["data"].get("provider_status")
            ev = ("readback", "APPROVED" if ps == "APPROVED" and not g[1] else
                  ("RECONCILIATION_PENDING" if g[1] and ps != "COMPLETED" else "APPROVAL_PENDING"))
        elif e["kind"] in STATE_OF:
            ev = ("readback", STATE_OF[e["kind"]])
        if ev is None:
            continue
        out["trace"].append(ev)
        g2 = gstep(g, ev)
        if g2 is None:
            v.append(f"C5 event {e['kind']} (seq {e['seq']}) not a gstep transition from {g[0]}")
            break
        g = g2
    out["gstep_final"] = g
    return out


def read_ledger(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    effects = [dict(r) for r in db.execute("SELECT * FROM effects")]
    events = [dict(r) for r in db.execute("SELECT * FROM events ORDER BY seq")]
    db.close()
    for e in events:
        e["data"] = json.loads(e["data"]) if isinstance(e["data"], str) else (e["data"] or {})
    return effects, events


def scenarios(tmp):
    from commerce_bridge.consolidated import install
    from commerce_bridge.qualified import QualifiedSources
    from . import sandbox_admission as S
    from .mission import RUNTIME_ROLES
    sources = QualifiedSources(ROOT / "runtime_sources", immutable=True)
    install(sources)
    ledgers = []
    try:
        for name, knobs, approve in [("normal", {}, True), ("lost_create_reply", {"lost_create": True}, False),
                                     ("lost_capture_reply", {"lost_capture": True}, True), ("capture_pending", {"pending": True}, True),
                                     ("capture_declined", {"declined": True}, True), ("never_approved", {}, False)]:
            now = int(time.time())
            owner = Ed25519PrivateKey.generate()
            rt = {r: Ed25519PrivateKey.generate() for r in RUNTIME_ROLES}
            rt_pub = {r: base64.b64encode(k.public_key().public_bytes_raw()).decode() for r, k in rt.items()}
            owner_pub = base64.b64encode(owner.public_key().public_bytes_raw()).decode()
            m = {"schema": S.MISSION_DOMAIN, "profile": "sandbox-demo-v1", "mission_id": f"FORMAL003-{name}", "contract_version": "1",
                 "currency": "USD", "cap_cents": 9900, "merchant_ids": ["TESTMERCHANT1"], "actor": "agent:procurement-executor",
                 "epoch": 1, "deadline": now + 3600, "obligation_ids": ["pay-b1"], "payer_obligation_ids": ["pay-b1"],
                 "federation_contract_hash": "c" * 64, "runtime_public_keys": rt_pub}
            c = {"schema": S.CONTROL_DOMAIN, "sequence": 1, "mission_id": m["mission_id"], "mission_hash": digest(S.MISSION_DOMAIN, m),
                 "epoch": 1, "active": True, "expires_at": now + 3600, "revoked_permits": [], "revoked_actors": []}
            m_env, c_env = raw_sign(owner, S.MISSION_DOMAIN, m), raw_sign(owner, S.CONTROL_DOMAIN, c)
            adm = S.SandboxDemoAdmission(owner_pub, rt_pub, sources, observer=S.fresh_observer(rt, sources))
            prov = ExternalStub()
            for k, val in knobs.items():
                setattr(prov, k, val)
            path = Path(tmp) / f"{name}.db"
            g = Gateway(path, adm, prov)
            g.apply_control(m["mission_id"], c_env)
            line = [{"service_id": "LMF-B1", "supplier_id": "owner:f1", "quote_id": "q-b1", "price_cents": 9900,
                     "effect_slot": "payer-b1", "obligation_id": "pay-b1"}]
            p_env, _, _ = S.issue_permit(adm, m_env, rt, line, 9900, sources)
            eid = g.reserve(m_env, p_env)
            steps = [g.create(eid)]
            if approve:
                prov.approve()
            for _ in range(2):          # retries: the Gateway must never re-dispatch an uncertain effect
                try:
                    steps.append(g.capture(eid))
                except Denied as e:
                    steps.append(f"DENY:{e}")
                try:
                    steps.append(g.create(eid))
                except Denied as e:
                    steps.append(f"DENY:{e}")
            steps.append(g.reconcile(eid))
            ledgers.append({"scenario": name, "path": str(path), "states": steps,
                            "provider_post_calls": [list(x[:2]) for x in prov.calls if x[0] == "POST"]})
    finally:
        sources.close()
    return ledgers


def main():
    out = Path(sys.argv[1])
    recorded = sys.argv[2:]
    res = {"scenarios": [], "recorded": []}
    with tempfile.TemporaryDirectory() as tmp:
        for sc in scenarios(tmp):
            effects, events = read_ledger(sc["path"])
            checks = [check_effect(e, events) for e in effects]
            sc["effects"] = checks
            sc["pass"] = all(not c["violations"] for c in checks) and \
                sum(1 for p in sc["provider_post_calls"] if "capture" in p[1]) <= 1 and \
                sum(1 for p in sc["provider_post_calls"] if p[1].endswith("/orders")) <= 1
            res["scenarios"].append(sc)
            print(("PASS " if sc["pass"] else "FAIL ") + sc["scenario"], sc["states"], len(sc["provider_post_calls"]), flush=True)
    for path in recorded:
        effects, events = read_ledger(path)
        checks = [check_effect(e, events, attestation_logged=False) for e in effects]
        res["recorded"].append({"ledger": path, "effects": checks, "pass": all(not c["violations"] for c in checks)})
        print(("PASS " if res["recorded"][-1]["pass"] else "FAIL ") + path, len(effects), flush=True)
    res["verdict"] = "PASS" if all(s["pass"] for s in res["scenarios"]) and all(r["pass"] for r in res["recorded"]) else "FAIL"
    out.write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps({"verdict": res["verdict"], "scenarios": len(res["scenarios"]), "recorded": len(res["recorded"])}))


if __name__ == "__main__":
    main()
