"""DEMO-006 sandbox-demo-v1 admission: genuine qualified Core/Lean decision + owner authority, driving
the UNCHANGED A04 Gateway (durable budget, idempotency, revocation, provider safety).

Separate from, and never valid as, the production admission contract:
  * own signature domains (commerce.sandbox-demo.*); production commerce.mission/permit.v1 are untouched;
  * production controls NOT part of this profile: independent acceptance (A03/A01/A02 packages),
    separately staffed admission issuer, CAGE/VEIP authorizations, installation manifest.
Exercised on every Gateway check (reserve, create, capture):
  owner Control-Key signature on the mission and current control; control active/epoch/expiry/
  revocation; permit signature and expiry; amount <= cap, payee in mission, actor; Core observation
  signature, binding to mission/intent/action, freshness; and a fresh evaluation by the qualified
  BCh runtime (Core directive ACT + qualified Lean decider verdict T for the exact action).
"""
import copy
import hashlib
import json
import time
from datetime import datetime, timezone

import rfc8785
from commerce_adapter.contracts import Denied, cents, digest, require, verify
from commerce_authority.custody import sign as raw_sign

MISSION_DOMAIN = "commerce.sandbox-demo.mission.v1"
CONTROL_DOMAIN = "commerce.sandbox-demo.control.v1"
PERMIT_DOMAIN = "commerce.sandbox-demo.permit.v1"
OBS_DOMAIN = "commerce.sandbox-demo.core-observation.v1"
MAX_AGE = 300
# Sandbox-profile permit lifetime (production A04: 600 s). Freshness of the Core state is enforced separately:
# every gate re-observes the current state and re-runs the qualified Lean decision (see fresh_observer).
PERMIT_TTL = 900


def b64pub(k):
    import base64
    return base64.b64encode(k.public_key().public_bytes_raw()).decode()


def plan_hash(final_lines):
    return digest("commerce.demo.plan.v1", final_lines)


def build_core_inputs(mission, final_lines, total_cents, runtime, sources, now):
    """World, records, intent, observation and policy for the qualified Core, from the actual run."""
    mid, actor, merchant, cap = mission["mission_id"], mission["actor"], mission["merchant_ids"][0], mission["cap_cents"]
    world = {"schema_version": "legion.world.v1", "world_id": mid, "initial_state": {"budget": {"available_cents": cap}},
             "tools": {"paypal.capture": {"kind": "write", "target": merchant, "params": {
                 "amount_cents": {"type": "integer", "min": 1, "max": cap}, "merchant_id": {"type": "string", "enum": [merchant]}},
                 "native_preconditions": []}},
             "roles": [{"agent_id": actor, "tasks": [
                 {"task_id": "observe", "kinds": ["READ"], "depends_on": []},
                 {"task_id": "analyze", "kinds": ["ANALYZE"], "depends_on": ["observe"]},
                 {"task_id": "pay", "kinds": ["WRITE"], "depends_on": ["observe", "analyze"], "write_tool": "paypal.capture",
                  "target": merchant, "expected_args": {"amount_cents": total_cents, "merchant_id": merchant}}]}]}
    records = [{"seq": 1, "kind": "OBSERVATION", "agent_id": actor, "task_id": "observe", "observation_id": "obs-quotes",
                "facts": {"available_cents": cap}},
               {"seq": 2, "kind": "ANALYSIS_RECORD", "agent_id": actor, "task_id": "analyze", "record_id": "analysis-final-plan",
                "facts": {"amount_cents": total_cents}, "evidence_refs": ["obs-quotes"]}]
    mh = digest(MISSION_DOMAIN, mission)
    intent = {"obligation_id": "pay", "effect_slot": "bundle-SVC-1-2-3", "actor": actor, "merchant_id": merchant,
              "quote_hash": plan_hash(final_lines), "prerequisites_hash": digest("commerce.prerequisites.v1", records),
              "currency": "USD", "amount_cents": total_cents}
    core_sha = hashlib.sha256(b"LEGION-MISSION-CONTRACT-v1\0" + json.dumps(world, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    command = hashlib.sha256(json.dumps({"mission": mid, "task_id": "pay", "epoch": 1}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    status = {"state": "RUNNING", "attempt_uid": f"{mid}-ATTEMPT-1", "world_instance_id": f"{mid}-WORLD", "epoch": 1,
              "contract_sha256": core_sha, "contract_version": 0}
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    reports = []
    for i in (1, 2, 3):
        body = {"fact_id": "pay:deps_closed", "agent": f"agent:reporter-{i}", "root": f"root-{i}", "value": True, "observed_at": stamp}
        reports.append({**body, "sig": runtime[f"reporter_{i}"].sign(rfc8785.dumps(body)).hex()})
    observation = {"schema": OBS_DOMAIN, "mission_hash": mh, "intent_hash": digest("commerce.intent.v1", intent), "epoch": 1,
                   "attempt_state": "RUNNING", "observed_at": int(now), "contract_hash": digest("commerce.demo.contract", world),
                   "quote_hash": intent["quote_hash"], "world": world, "world_state": copy.deepcopy(world["initial_state"]), "task_id": "pay",
                   "records": records, "effects": [],
                   "controller_state": {"pay": {"status": "PENDING", "command_id": None, "epoch": 0, "effect_id": None, "unknown": False}},
                   "action": {"tool": "paypal.capture", "target": merchant, "params": {"amount_cents": total_cents, "merchant_id": merchant}},
                   "reports": reports, "claimed_directive": {"directive": "ACT", "task_id": "pay", "epoch": 1, "command_id": command},
                   "core_boundary": {"status": status, "permit_verified": True,
                                     "permit": {"permit_id": f"{mid}-CORE-PERMIT", "attempt_uid": status["attempt_uid"],
                                                "world_instance_id": status["world_instance_id"], "mission": core_sha, "epoch": 1, "task_id": "pay",
                                                "command_id": command, "actor": actor, "target": merchant, "nonce": f"{mid}-NONCE"},
                                     "used_nonces": [], "fenced": [], "gate_records": [], "readback_records": []}}
    policy = {"f": 1, "max_age_seconds": MAX_AGE,
              "reporters": [{"agent": f"agent:reporter-{i}", "root": f"root-{i}", "basis": f"basis-{i}", "source_id": f"DEMO-REPORTER-{i}",
                             "public_key": b64pub(runtime[f"reporter_{i}"])} for i in (1, 2, 3)]}
    return world, records, intent, observation, policy


class SandboxDemoAdmission:
    """Implements the Admission interface the A04 Gateway calls: control(envelope), check(mission, permit, control)."""

    def __init__(self, owner_public_key, runtime_public_keys, sources, clock=time.time, observer=None):
        self.owner, self.rt, self.sources, self.clock = owner_public_key, runtime_public_keys, sources, clock
        # observer(mission, permit, now) -> signed Core observation of the CURRENT state (DEMO-006 fix after mission 1:
        # a decision fixed at issue time went stale while a human approved in PayPal).
        self.observer = observer
        self.trust = {"control": owner_public_key}
        self.last_decision = None

    def control(self, envelope):
        p = verify(envelope, self.owner, CONTROL_DOMAIN)
        require(p["schema"] == CONTROL_DOMAIN and type(p["sequence"]) is int and p["sequence"] >= 0 and type(p["epoch"]) is int, "CONTROL_SCHEMA")
        return p

    def evaluate(self, observation, policy, intent, now):
        """Fresh qualified evaluation: Core (pinned d9d38cd methods) + qualified Lean decider 99cda684 + zverdict witness."""
        calc = self.sources.session.calculate(observation, policy, now)
        q = calc.get("qualified", {})
        decision = {"lean_verdict": q.get("lean", {}).get("verdict"), "lean_grounds": q.get("lean", {}).get("grounds"),
                    "runtime_verdict": q.get("runtime", {}).get("verdict"), "alpha_payload_hash": q.get("payload_hash"),
                    "directive": calc["directive"], "all_facts_true": calc["all_facts_true"], "predicate_truths": calc["predicate_truths"],
                    "submissions": calc["submissions"], "task_actor": calc["task_actor"], "parameter_status": calc["parameter_status"],
                    "native_preconditions_ok": calc["native_preconditions_ok"], "differential": calc["differential"],
                    "profile_sha256": q.get("profile_sha256"), "evaluated_at": now}
        require(all(s == "ACCEPTED_FOR_EVALUATION" for s in calc["submissions"]), "CORE_REPORT_SIGNATURE_OR_IDENTITY")
        require(calc["task_actor"] == intent["actor"] and calc["directive"]["directive"] == "ACT", "CORE_NOT_ACT")
        require(calc["parameter_status"] == 200 and calc["native_preconditions_ok"], "CORE_TOOL_PRECONDITIONS")
        require(calc["all_facts_true"] and all(calc["predicate_truths"]), "CORE_NOT_EARNED_TRUE")
        require(observation["claimed_directive"] == calc["directive"], "CORE_DIRECTIVE_MISMATCH")
        require(decision["lean_verdict"] == "T", "QUALIFIED_LEAN_NOT_T")
        self.last_decision = decision
        return decision

    def check_observation(self, m, mh, envelope, intent, now):
        obs = verify(envelope, self.rt["core_observer"], OBS_DOMAIN)
        require(obs["schema"] == OBS_DOMAIN and obs["mission_hash"] == mh and obs["intent_hash"] == digest("commerce.intent.v1", intent)
                and obs["epoch"] == m["epoch"] and obs["attempt_state"] == "RUNNING", "CORE_OBSERVATION_BINDING")
        # The observation-level checks of production Bridge.assess (same codes): the Core/Lean decision does not cover them.
        for state in obs["controller_state"].values():
            require(set(state) == {"status", "command_id", "epoch", "effect_id", "unknown"}
                    and state["status"] in ("PENDING", "CLAIMED", "CLOSED") and state["unknown"] is False
                    and type(state["epoch"]) is int and state["epoch"] >= 0, "UNKNOWN_OR_MALFORMED_CORE_STATE")
        sequences = [record["seq"] for record in obs["records"]]
        require(all(type(n) is int and n >= 0 for n in sequences) and sequences == sorted(set(sequences)), "CORE_RECORD_CHRONOLOGY")
        require(digest("commerce.prerequisites.v1", obs["records"]) == intent["prerequisites_hash"], "PREREQUISITE_RECORDS_MISMATCH")
        seen = {}
        for report in obs["reports"]:
            key = (report["fact_id"], report["agent"])
            require(key not in seen or seen[key] is report["value"], "REPORTER_EQUIVOCATION")
            seen[key] = report["value"]
        require(0 <= now - obs["observed_at"] <= MAX_AGE, "STALE_CORE_OBSERVATION")
        a = obs["action"]
        require(a["tool"] == "paypal.capture" and a["target"] == intent["merchant_id"] and a["params"]["amount_cents"] == intent["amount_cents"]
                and a["params"]["merchant_id"] == intent["merchant_id"], "CORE_ACTION_BINDING")
        require(obs["world"]["world_id"] == m["mission_id"] and obs["quote_hash"] == intent["quote_hash"], "CORE_WORLD_OR_PLAN_BINDING")
        return obs

    def check(self, mission_envelope, permit_envelope, control):
        now = self.clock()
        m = verify(mission_envelope, self.owner, MISSION_DOMAIN)
        require(m["schema"] == MISSION_DOMAIN and m["profile"] == "sandbox-demo-v1", "SCHEMA")
        p = verify(permit_envelope, self.rt["sandbox_admission"], PERMIT_DOMAIN)
        mh = digest(MISSION_DOMAIN, m)
        require(control["mission_hash"] == mh and control["active"] is True and control["expires_at"] > now, "CONTROL_INACTIVE")
        require(m["currency"] == "USD" and type(m["cap_cents"]) is int and 0 < m["cap_cents"] <= 50000, "MISSION_BUDGET")
        require(m["deadline"] > now and p["issued_at"] <= now < p["expires_at"] <= p["issued_at"] + PERMIT_TTL, "EXPIRED_PERMIT")
        require(m["epoch"] == p["epoch"] == control["epoch"], "STALE_EPOCH")
        require(p["permit_id"] not in control["revoked_permits"], "REVOKED_PERMIT")
        require(m["actor"] not in control.get("revoked_actors", []), "REVOKED_ACTOR")
        require(p["mission_hash"] == mh and p["schema"] == PERMIT_DOMAIN, "PERMIT_BINDING")
        intent = p["intent"]
        cents(intent["amount_cents"])
        require(intent["amount_cents"] <= m["cap_cents"] and intent["merchant_id"] in m["merchant_ids"] and intent["currency"] == m["currency"]
                and intent["actor"] == m["actor"] and intent["obligation_id"] == "pay", "INTENT_SCOPE_OR_BUDGET")
        issued = verify(p["observation"], self.rt["core_observer"], OBS_DOMAIN)       # issue-time observation still authentic and bound
        require(issued["intent_hash"] == digest("commerce.intent.v1", intent) and issued["mission_hash"] == mh, "CORE_OBSERVATION_BINDING")
        current = self.observer(m, p, now) if self.observer is not None else p["observation"]
        obs = self.check_observation(m, mh, current, intent, now)
        # Fresh qualified decision at every gate (reserve / create / capture) on the current observation; never a cached verdict.
        self.evaluate(obs, p["policy"], intent, now)
        # Gateway compatibility: it derives the effect key from these fields.
        m = dict(m); m.setdefault("effects", [intent])
        return m, p, intent


def fresh_observer(runtime, sources):
    """Core observer service: re-observes the current state for the permitted action (fresh reporter attestations)."""
    def observe(m, p, now):
        _, _, intent, observation, policy = build_core_inputs(m, p["plan_lines"], p["intent"]["amount_cents"], runtime, sources, int(now))
        require(intent == p["intent"] and policy == p["policy"], "CURRENT_STATE_DIFFERS_FROM_PERMIT")
        return raw_sign(runtime["core_observer"], OBS_DOMAIN, observation)
    return observe


def issue_permit(adm, mission_env, runtime, final_lines, total_cents, sources, now=None):
    """The sandbox admission component: issues a permit ONLY after a fresh Lean T for the exact action."""
    now = int(now or time.time())
    m = verify(mission_env, adm.owner, MISSION_DOMAIN)
    world, records, intent, observation, policy = build_core_inputs(m, final_lines, total_cents, runtime, sources, now)
    obs_env = raw_sign(runtime["core_observer"], OBS_DOMAIN, observation)
    adm.check_observation(m, digest(MISSION_DOMAIN, m), obs_env, intent, now)
    decision = adm.evaluate(observation, policy, intent, now)
    permit = {"schema": PERMIT_DOMAIN, "permit_id": f"{m['mission_id']}-PERMIT-1", "issued_at": now, "expires_at": now + PERMIT_TTL,
              "epoch": m["epoch"], "mission_hash": digest(MISSION_DOMAIN, m), "intent": intent, "plan_lines": final_lines,
              "observation": obs_env, "policy": policy, "lean_decision_at_issue": decision,
              "profile": "sandbox-demo-v1", "scope": "paypal.sandbox.orders.create_capture"}
    return raw_sign(runtime["sandbox_admission"], PERMIT_DOMAIN, permit), decision, observation
