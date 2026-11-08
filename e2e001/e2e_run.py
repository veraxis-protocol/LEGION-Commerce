"""WO-COM-E2E-001 orchestrator: one procurement attempt, end to end.

  python -B -m e2e001.e2e_run [--intent "..."]

User intent -> agent proposals (NVIDIA NIM, LEGION LLM interface) -> genuine Channel3 offers -> chair selection
-> live re-quote / changed offer -> replanning -> qualified Core + Lean admission (legion_decide 99cda684) ->
authorized purchase plan -> A04 Gateway -> payment provider -> independent readback -> Judge (separate process).

PAYMENT PROVIDER. A native PayPal Sandbox payment needs a genuine independent A03 acceptance, current signed
permits and the owner's buyer approval. None of these exist (A03 is unsigned: REVIEW_REQUEST_v5_UNSIGNED), so this
run uses the synthetic provider (tests.helpers.FakePayPal, evidence_mode MOCK_ONLY) and the authority keys are
ephemeral TEST keys created in memory for this run (never persisted, never the owner's Control Key). Everything
payment-side is labelled MECHANISM_ONLY. Channel3 offers, NIM agent calls and the qualified Lean decisions are
real. No PayPal credential is read and no PayPal network call is made.
"""
import argparse
import base64
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from commerce_adapter.contracts import Denied, digest, require
from commerce_adapter.gateway import Gateway
from commerce_authority.custody import sign as raw_sign
from commerce_bridge.consolidated import install
from commerce_bridge.qualified import QualifiedSources, QUALIFIED, QUALIFIED_DECIDE_SHA256
from commerce_demo import nim, sandbox_admission as S
from commerce_demo.events import Log
from tests.helpers import FakePayPal

from .channel3 import Channel3, offers_of

ROOT = Path(__file__).resolve().parent.parent
RUNS = Path(__file__).resolve().parent / "runs"
CAP_CENTS = 50000
MERCHANT = "E2ETESTMERCHANT"          # synthetic payee standing in for the seller (MECHANISM_ONLY)
ACTOR = "agent:procurement-executor"
RUNTIME_ROLES = ("core_observer", "sandbox_admission", "reporter_1", "reporter_2", "reporter_3", "fulfillment_simulator")
DEFAULT_INTENT = ("Procure one pair of over-ear active noise-cancelling wireless headphones for an open-plan office worker. "
                  "New, in stock, sold in USD by a US online seller, total price at most $500.")


def b64(k):
    return base64.b64encode(k.public_key().public_bytes_raw()).decode()


def write(run, name, value):
    (run / name).write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")


def cents_of(price):
    return int(round(float(price) * 100))


class Agents:
    """Model-driven agents. Every call is recorded verbatim (messages, model, response id, content, usage)."""

    def __init__(self, run, log):
        self.trace = (run / "AGENT_TRACE.jsonl").open("a", encoding="utf-8", newline="\n")
        self.log = log

    def call(self, agent, purpose, messages, schema, temperature=0.4, tries=2):
        for attempt in range(tries):
            parsed = self._call(agent, purpose, messages, schema, temperature, attempt)
            if parsed is not None:
                return parsed
        return None

    def _call(self, agent, purpose, messages, schema, temperature, attempt):
        # Guided json_schema decoding is used for planners; the chair answers free-form JSON (guided decoding stalled in
        # whitespace after the rationale in runs 160036Z/160158Z) and is validated here against the same schema.
        guided = schema if not purpose.startswith("selection") else None
        r = nim.chat(messages, schema=guided, temperature=temperature, max_tokens=1200 if guided is None else 2500)
        parsed, err = None, None
        try:
            text = (r.get("content") or "").strip()
            parsed, _ = json.JSONDecoder().raw_decode(text[text.index("{"):])
            for k in schema["required"]:
                if k not in parsed:
                    raise ValueError(f"missing {k}")
            enum = schema["properties"].get("offer_id", {}).get("enum")
            if enum is not None and parsed["offer_id"] not in enum:
                raise ValueError("offer_id not in table")
        except ValueError as e:
            parsed, err = None, f"UNPARSEABLE_OR_INVALID: {e}"
        rec = {"agent": agent, "purpose": purpose, "messages": messages, "schema": schema, "guided_decoding": guided is not None, "temperature": temperature,
               "response": {k: r.get(k) for k in ("provider", "model", "response_id", "content", "finish_reason", "usage", "latency_s", "failed", "error", "status")},
               "parsed": parsed, "parse_error": err, "attempt": attempt, "at": time.time()}
        self.trace.write(json.dumps(rec, default=str) + "\n"); self.trace.flush()
        self.log.emit("AGENT_CALL", agent, purpose=purpose, model=r.get("model"), response_id=r.get("response_id"), ok=parsed is not None)
        return parsed


PROPOSAL_SCHEMA = {"type": "object", "properties": {
    "query": {"type": "string"}, "max_price_usd": {"type": "number"},
    "must_have": {"type": "array", "items": {"type": "string"}}, "rationale": {"type": "string", "maxLength": 300}},
    "required": ["query", "max_price_usd", "must_have", "rationale"]}


def selection_schema(keys):
    return {"type": "object", "properties": {"offer_id": {"type": "string", "enum": keys}, "rationale": {"type": "string", "maxLength": 300},
                                             "expected_price_usd": {"type": "number"}},
            "required": ["offer_id", "rationale", "expected_price_usd"]}


def propose(agents, intent, cap):
    styles = {"agent:planner-1": "Prioritise best value for money.", "agent:planner-2": "Prioritise strongest noise cancellation and comfort.",
              "agent:planner-3": "Prioritise well-known brands with high ratings."}
    out = []
    for a, style in styles.items():
        msg = [{"role": "system", "content": f"You are {a}, a procurement planner in the LEGION collective. {style} You never buy anything; "
                                             "you only propose a product search for the Channel3 catalogue. Answer with JSON only."},
               {"role": "user", "content": f"User intent: {intent}\nHard budget cap: ${cap / 100:.2f} USD total.\n"
                                           "Propose ONE concise product search query (3-8 words), a max price in USD (<= cap) and must-have features."}]
        p = agents.call(a, "proposal", msg, PROPOSAL_SCHEMA)
        if p:
            p["max_price_usd"] = min(float(p.get("max_price_usd") or cap / 100), cap / 100)
            out.append({"agent": a, **p})
    return out


def eligible(o, cap):
    return (o["currency"] == "USD" and o["availability"] == "InStock" and isinstance(o["price"], (int, float))
            and 0 < cents_of(o["price"]) <= cap and (o["condition"] in (None, "new")))


def table_for_llm(offers):
    # Short ids: in run 160349Z the model dropped the '#domain' part of composite offer keys.
    return "\n".join(f"O{i} | {o['title'][:90]} | brand {o['brand']} | seller {o['seller']} | ${o['price']:.2f} {o['currency']} | "
                     f"{o['availability']} | rating {o['rating']} ({o['rating_count']})" for i, o in enumerate(offers, 1))


def chair(agents, intent, cap, offers, note=None, round_name="initial"):
    ids = {f"O{i}": o["offer_key"] for i, o in enumerate(offers, 1)}
    msg = [{"role": "system", "content": "You are agent:chair of the LEGION procurement collective. Select exactly one offer from the table that "
                                         "best satisfies the user intent within the hard budget cap. You do not pay; the payment is decided "
                                         "later by formal admission. Answer with compact JSON only; rationale at most two sentences. Format: {\"offer_id\": \"<O-number from table>\", \"rationale\": \"...\", \"expected_price_usd\": <number>}"},
           {"role": "user", "content": f"User intent: {intent}\nHard cap: ${cap / 100:.2f} USD.\n" + (f"NOTE: {note}\n" if note else "") +
                                       f"Offers (offer_id | title | brand | seller | price | availability | rating):\n{table_for_llm(offers)}"}]
    sel = agents.call("agent:chair", f"selection-{round_name}", msg, selection_schema(list(ids)), temperature=0.2)
    if sel:
        sel["offer_key"] = ids[sel["offer_id"]]
        sel["offer_table"] = ids
    return sel


def requote(c3, offer, label):
    """Live re-quote of the exact offer (same product, same seller domain) from Channel3."""
    p, meta = c3.product(offer["product_id"], label=label)
    for o in offers_of(p, meta):
        if o["seller"] == offer["seller"]:
            return o, meta
    return None, meta


def mint_keys():
    owner = Ed25519PrivateKey.generate()
    runtime = {r: Ed25519PrivateKey.generate() for r in RUNTIME_ROLES}
    return owner, runtime


def make_mission(run_id, intent, owner, runtime, now):
    mission = {"schema": S.MISSION_DOMAIN, "profile": "sandbox-demo-v1", "mission_id": f"E2E-001-{run_id[4:]}", "contract_version": "1",
               "statement": intent, "obligations": [{"id": "pay", "service": "one product matching the intent", "requirement": "genuine Channel3 offer, InStock, USD, total <= cap"}],
               "obligation_ids": ["pay"], "currency": "USD", "cap_cents": CAP_CENTS, "merchant_ids": [MERCHANT], "actor": ACTOR,
               "epoch": 1, "created_at": now, "deadline": now + 2 * 3600, "max_attempts": 1,
               "authority": "TEST-E2E-001 ephemeral keys (MECHANISM_ONLY; not the owner's Control Key; not A03)",
               "disruption_protocol": "After the chair's first selection the exact offer is re-quoted live from Channel3. If the live offer changed "
                                      "(price or availability) the change is used as-is (GENUINE). If it did not change, the disruption injector "
                                      "publishes a labelled CHANGED_OFFER raising that offer's price to 110% of the cap (INJECTED). The plan must be "
                                      "rejected and the collective must replan without that offer.",
               "runtime_public_keys": {r: b64(k) for r, k in runtime.items()}}
    control = {"schema": S.CONTROL_DOMAIN, "sequence": 1, "mission_id": mission["mission_id"], "mission_hash": digest(S.MISSION_DOMAIN, mission),
               "epoch": 1, "active": True, "expires_at": mission["deadline"], "revoked_permits": [], "revoked_actors": []}
    return mission, raw_sign(owner, S.MISSION_DOMAIN, mission), raw_sign(owner, S.CONTROL_DOMAIN, control)


def quote_gate(c3, line, log, counter):
    """Fresh-price gate added in front of the Core observer: every admission check re-quotes the offer live."""
    def gate(m, p, now):
        counter["n"] += 1
        live, meta = requote(c3, line, f"gate_requote_{counter['n']}")
        ok = live is not None and live["availability"] == "InStock" and cents_of(live["price"]) == line["price_cents"]
        log.emit("PRICE_GATE", "admission:quote-gate", offer_key=line["offer_key"], plan_price_cents=line["price_cents"],
                 live_price_cents=cents_of(live["price"]) if live else None, live_availability=live and live["availability"],
                 raw_response_sha256=meta["response_sha256"], ok=ok)
        require(ok, "STALE_PRICE_OR_OFFER_CHANGED")
    return gate


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--intent", default=DEFAULT_INTENT); a = ap.parse_args()
    now = int(time.time())
    run_id = time.strftime("run-%Y%m%dT%H%M%SZ", time.gmtime(now))
    run = RUNS / run_id; run.mkdir(parents=True)
    owner, runtime = mint_keys()
    mission, m_env, c_env = make_mission(run_id, a.intent, owner, runtime, now)
    attempt = mission["mission_id"] + "-ATTEMPT-1"
    log = Log(run / "E2E_EVENTS.jsonl", attempt)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    write(run, "RUN_MANIFEST.json", {"wo": "WO-COM-E2E-001", "run_id": run_id, "attempt_id": attempt, "source_commit": head,
                                     "dirty": bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True).strip()),
                                     "python": sys.version, "llm": nim.MODEL, "core_commit": QUALIFIED, "decider_sha256": QUALIFIED_DECIDE_SHA256,
                                     "payment_evidence_mode": "MECHANISM_ONLY (FakePayPal; no PayPal network call)",
                                     "authority": "ephemeral TEST keys, private keys never persisted",
                                     "native_blockers": ["no genuine independent A03 acceptance (REVIEW_REQUEST_v5 is UNSIGNED)",
                                                         "no current owner-signed production mission/permit", "no owner buyer approval"]})
    write(run, "MISSION.json", {"mission_envelope": m_env, "control_envelope": c_env, "owner_test_public_key": b64(owner),
                                "mission_hash": digest(S.MISSION_DOMAIN, mission)})
    log.emit("INTENT", "user", intent=a.intent, cap_cents=CAP_CENTS)
    agents = Agents(run, log)
    c3 = Channel3(run / "CHANNEL3_RAW")
    sources = QualifiedSources(ROOT / "runtime_sources", immutable=True); install(sources, hot_standby_seconds=60.0)
    try:
        # 1. agent proposals
        proposals = propose(agents, a.intent, CAP_CENTS)
        for p in proposals:
            log.emit("PROPOSAL", p["agent"], query=p["query"], max_price_usd=p["max_price_usd"], must_have=p["must_have"])
        # 2. genuine Channel3 offers
        offers, seen = [], set()
        for i, p in enumerate(proposals, 1):
            resp, meta = c3.search(p["query"], max_price=p["max_price_usd"], limit=10, label=f"search_planner{i}")
            log.emit("CHANNEL3_SEARCH", "backend:channel3", query=p["query"], products=len(resp["products"]), raw_response_sha256=meta["response_sha256"])
            for prod in resp["products"]:
                for o in offers_of(prod, meta):
                    if o["offer_key"] not in seen and eligible(o, CAP_CENTS):
                        seen.add(o["offer_key"]); offers.append({**o, "found_by": p["agent"]})
        offers = offers[:15]
        write(run, "OFFERS.json", {"notice": "Channel3 product listings (discovery only; nothing purchased)", "count": len(offers), "offers": offers})
        log.emit("OFFERS", "backend:channel3", count=len(offers), keys=[o["offer_key"] for o in offers])
        require(len(offers) >= 3, "FEWER_THAN_THREE_GENUINE_OFFERS")
        by_key = {o["offer_key"]: o for o in offers}
        # 3. selection
        s1 = chair(agents, a.intent, CAP_CENTS, offers)
        require(s1 and s1["offer_key"] in by_key, "CHAIR_SELECTION_INVALID")
        first = by_key[s1["offer_key"]]
        log.emit("PLAN_SELECTED", "agent:chair", round="initial", offer_key=first["offer_key"], price=first["price"], rationale=s1["rationale"])
        # 4. changed offer -> recheck -> replan
        live, meta = requote(c3, first, "requote_initial")
        changed = live is None or live["availability"] != "InStock" or cents_of(live["price"]) != cents_of(first["price"])
        if changed:
            change = {"change_type": "GENUINE", "offer_key": first["offer_key"], "old_price": first["price"], "new_price": live and live["price"],
                      "new_availability": live and live["availability"], "raw_response_sha256": meta["response_sha256"]}
        else:
            change = {"change_type": "INJECTED", "offer_key": first["offer_key"], "old_price": first["price"],
                      "new_price": round(CAP_CENTS * 1.10 / 100, 2), "new_availability": "InStock",
                      "live_requote_unchanged": {"price": live["price"], "raw_response_sha256": meta["response_sha256"]},
                      "injector": "e2e001 disruption injector (preregistered in mission.disruption_protocol)"}
        log.emit("CHANGED_OFFER", "disruption-injector" if change["change_type"] == "INJECTED" else "backend:channel3", **change)
        new_cents = cents_of(change["new_price"]) if change["new_price"] is not None else None
        reasons = []
        if new_cents is None or change["new_availability"] != "InStock":
            reasons.append("OFFER_UNAVAILABLE")
        elif new_cents > CAP_CENTS:
            reasons.append("EXCEEDS_CAP")
        elif new_cents != cents_of(first["price"]):
            reasons.append("PRICE_CHANGED")
        log.emit("PLAN_RECHECKED", "agent:coordinator", offer_key=first["offer_key"], reasons=reasons, rejected=bool(reasons))
        require(reasons, "RECHECK_DID_NOT_REJECT_CHANGED_OFFER")
        log.emit("PLAN_REJECTED", "agent:coordinator", offer_key=first["offer_key"], reasons=reasons)
        remaining = [o for o in offers if o["offer_key"] != first["offer_key"]]
        s2 = chair(agents, a.intent, CAP_CENTS, remaining, round_name="replan",
                   note=f"Offer {first['offer_key']} was rejected ({', '.join(reasons)}) after it changed to ${change['new_price']}. Replan without it.")
        require(s2 and s2["offer_key"] in by_key and s2["offer_key"] != first["offer_key"], "REPLAN_INVALID")
        final = by_key[s2["offer_key"]]
        log.emit("PLAN_SELECTED", "agent:chair", round="replan", offer_key=final["offer_key"], price=final["price"], rationale=s2["rationale"])
        live2, meta2 = requote(c3, final, "requote_final")
        require(live2 is not None and live2["availability"] == "InStock" and cents_of(live2["price"]) <= CAP_CENTS, "FINAL_OFFER_NOT_CURRENT")
        line = {"obligation_id": "pay", "offer_key": final["offer_key"], "product_id": final["product_id"], "title": final["title"],
                "seller": final["seller"], "price_cents": cents_of(live2["price"]), "currency": "USD", "quote_fetched_at": int(time.time()),
                "quote_raw_response_sha256": meta2["response_sha256"]}
        log.emit("FINAL_PLAN", "agent:coordinator", **line)
        write(run, "PLAN_TRACE.json", {"proposals": proposals, "selection_initial": s1, "changed_offer": change, "recheck_reasons": reasons,
                                       "selection_replan": s2, "final_line": line})
        # 5. formal admission
        counter = {"n": 0}
        gate = quote_gate(c3, line, log, counter)
        base_observer = S.fresh_observer(runtime, sources)

        def observer(m, p, t):
            gate(m, p, t)
            return base_observer(m, p, t)
        adm = S.SandboxDemoAdmission(b64(owner), mission["runtime_public_keys"], sources, observer=observer)
        p_env, decision, observation = S.issue_permit(adm, m_env, runtime, [line], line["price_cents"], sources)
        write(run, "LEAN_AUTHORIZATION.json", {"profile": "sandbox-demo-v1 (TEST authority, MECHANISM_ONLY)", "verdict": decision["lean_verdict"],
                                               "decision": decision, "permit_envelope": p_env, "core_commit": QUALIFIED,
                                               "decider_sha256": QUALIFIED_DECIDE_SHA256, "action": observation["action"]})
        log.emit("LEAN_DECISION", "qualified-core", verdict=decision["lean_verdict"], directive=decision["directive"]["directive"],
                 amount_cents=line["price_cents"], action_digest=decision["action_digest"], permit_id=p_env["payload"]["permit_id"])
        # 6. payment (synthetic provider)
        provider = FakePayPal()
        g = Gateway(run / "ledger.db", adm, provider)
        g.apply_control(mission["mission_id"], c_env)
        eid = g.reserve(m_env, p_env)
        st = g.create(eid)
        log.emit("ORDER_CREATED", "a04-gateway", effect_id=eid, state=st, evidence_mode="MECHANISM_ONLY")
        provider.approve()
        log.emit("BUYER_APPROVAL", "synthetic-buyer", evidence_mode="MECHANISM_ONLY", note="FakePayPal.approve(); no human PayPal approval")
        st = g.capture(eid)
        st2 = g.capture(eid)    # duplicate capture attempt inside the run: must not dispatch a second capture
        log.emit("PAYMENT_STATE", "a04-gateway", effect_id=eid, state=st, duplicate_capture_state=st2, capture_posts=provider.capture_count())
        # 7. export provider state for the independent readback process
        write(run, "PROVIDER_STATE_MECHANISM_ONLY.json", {"evidence_mode": "MECHANISM_ONLY", "orders": provider.orders, "captures": provider.details,
                                                          "calls": provider.calls})
        con = sqlite3.connect(run / "ledger.db"); con.row_factory = sqlite3.Row
        effects = [{k: r[k] for k in ("id", "mission_id", "obligation_id", "permit_id", "amount", "held", "state", "order_id", "capture_id", "capture_started")}
                   for r in con.execute("SELECT * FROM effects")]
        evs = [{"seq": r["seq"], "at": r["at"], "effect_id": r["effect_id"], "kind": r["kind"], "data": json.loads(r["data"])}
               for r in con.execute("SELECT * FROM events ORDER BY seq")]
        con.close()
        write(run, "BUDGET_LEDGER.json", {"cap_cents": CAP_CENTS, "effects": effects, "ledger_events": evs,
                                          "held_total_cents": sum(e["amount"] for e in effects if e["held"]),
                                          "network_posts": sum(1 for e in evs if e["kind"] == "NETWORK" and e["data"].get("method") == "POST")})
        log.emit("PRICE_GATE_SUMMARY", "admission:quote-gate", live_requotes=counter["n"])
    finally:
        sources.close()
    subprocess.run([sys.executable, "-B", "-m", "e2e001.readback", str(run)], cwd=ROOT, check=True)
    log.emit("PHASE", "agent:coordinator", phase="JUDGE")
    subprocess.run([sys.executable, "-B", "-m", "e2e001.judge", str(run)], cwd=ROOT)
    v = json.loads((run / "JUDGE_VERDICT.json").read_text())
    print(json.dumps({"run": str(run), "verdict": v["verdict"], "evidence_mode": v["evidence_mode"], "failed_checks": v["failed"]}))


if __name__ == "__main__":
    main()
