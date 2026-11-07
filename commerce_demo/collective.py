"""DEMO-006 collective: 100 registered LEGION agents; proposals, price shock and autonomous replanning.

Decider labels follow LEGION struct_agent conventions:
  nim       - real NVIDIA NIM inference; prompt, response id and output recorded
  scripted  - deterministic coordination code (SCRIPTED_DECISION, never presented as model intelligence)
  standby   - registered, idle, no decisions
No plan is authored by the developer: plans come only from model agents; deterministic agents only
read quotes, check constraints and record.
"""
import concurrent.futures as cf
import json
import time

from . import catalog, nim

PERSONAS = {
    "agent:planner-1": "cost minimizer: the cheapest complete plan that still meets every requirement",
    "agent:planner-2": "quality maximizer: the highest supplier ratings that fit the budget",
    "agent:planner-3": "speed focused: the fastest total delivery that fits the budget",
    "agent:planner-4": "risk balanced: strong ratings with headroom under the budget for price changes",
    "agent:planner-5": "value optimizer: best rating per dollar across the three services",
}
SCRIPTED = "SCRIPTED_DECISION_COORDINATION_NOT_A_MODEL_AGENT"


def registry():
    agents = [{"id": "agent:coordinator", "decider": "scripted", "role": "broadcasts mission, sequences phases"}]
    agents += [{"id": f"agent:quote-collector-{i}", "decider": "scripted", "role": f"reads the supplier feed for SVC-{i}"} for i in (1, 2, 3)]
    agents += [{"id": a, "decider": "nim", "role": "planner: " + p} for a, p in PERSONAS.items()]
    agents += [{"id": "agent:chair", "decider": "nim", "role": "selects one feasible plan and justifies selection/rejections"},
               {"id": "agent:price-monitor", "decider": "scripted", "role": "re-reads quotes before authorization, detects changes"},
               {"id": "agent:budget-auditor", "decider": "scripted", "role": "independently checks plans against current quotes and constraints"},
               {"id": "agent:procurement-executor", "decider": "scripted", "role": "Core task actor: records observation/analysis, requests payment"}]
    agents += [{"id": f"agent:reporter-{i}", "decider": "scripted", "role": f"ZTL evidence reporter (independent basis {i})"} for i in (1, 2, 3)]
    n = 100 - len(agents)
    agents += [{"id": f"agent:standby-{i:03d}", "decider": "standby", "role": "registered worker, standby"} for i in range(1, n + 1)]
    assert len(agents) == 100
    return agents


def plan_schema(feed):
    return {"type": "object", "properties": {
    "strategy": {"type": "string", "maxLength": 120},
    "lines": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "object", "properties": {
        "service_id": {"type": "string", "enum": ["SVC-1", "SVC-2", "SVC-3"]},
        "supplier_id": {"type": "string", "enum": sorted({q["supplier_id"] for q in feed["quotes"]})},
        "quote_id": {"type": "string", "enum": [q["quote_id"] for q in feed["quotes"]]}, "price_cents": {"type": "integer"}},
        "required": ["service_id", "supplier_id", "quote_id", "price_cents"]}},
    "total_cents": {"type": "integer"}, "rationale": {"type": "string", "maxLength": 400},
    "rejected_alternatives": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 160}}},
    "required": ["strategy", "lines", "total_cents", "rationale", "rejected_alternatives"]}

CHAIR_SCHEMA = {"type": "object", "properties": {
    "selected_plan_id": {"type": "string"}, "justification": {"type": "string", "maxLength": 500},
    "rejections": {"type": "array", "items": {"type": "object", "properties": {"plan_id": {"type": "string"}, "reason": {"type": "string"}},
                                               "required": ["plan_id", "reason"]}}},
    "required": ["selected_plan_id", "justification", "rejections"]}


def _quotes_table(feed):
    return [{k: q[k] for k in ("quote_id", "service_id", "supplier_id", "supplier", "price_cents", "rating_x10", "delivery_days")} for q in feed["quotes"]]


def planner_messages(agent, mission, feed, note=None, avoid=None):
    sys_msg = (f"You are {agent}, a procurement planning agent in the LEGION collective. Strategy: {PERSONAS[agent]}. "
               "Propose ONE complete procurement plan as JSON. Use only quotes from the table, copy price_cents exactly, "
               "include exactly one line for each of SVC-1, SVC-2, SVC-3, and state total_cents as the exact sum. "
               "Answer with compact single-line JSON only: rationale at most 300 characters, at most 3 short rejected_alternatives.")
    user = {"mission": mission["statement"], "obligations": mission["obligations"], "budget_cap_cents": mission["cap_cents"],
            "requirements": "every chosen supplier must have rating_x10 >= 40 and delivery_days <= 10; total_cents <= budget_cap_cents",
            "supplier_quotes_SIMULATED": _quotes_table(feed)}
    if note: user["situation_update"] = note
    if avoid: user["must_differ_from_these_supplier_combinations"] = avoid
    return [{"role": "system", "content": sys_msg}, {"role": "user", "content": json.dumps(user)}]


def call_agent(log, agent, purpose, messages, schema, temperature):
    started = time.time()
    r = nim.chat(messages, schema=schema, temperature=temperature)
    parsed = None
    if not r.get("failed"):
        try: parsed = json.loads(r["content"])
        except (TypeError, ValueError): parsed = None
    log["activity"].emit("NIM_CALL", agent, purpose=purpose, decider="nim", model=r.get("model"), response_id=r.get("response_id"),
                         messages=messages, output=r.get("content"), enable_thinking=r.get("enable_thinking"), parsed_ok=parsed is not None, usage=r.get("usage"),
                         finish_reason=r.get("finish_reason"), latency_s=r.get("latency_s"), failed=r.get("failed", False),
                         error=r.get("error"), wall_s=round(time.time() - started, 2))
    return parsed, r


def propose_round(log, mission, feed, round_name, note=None, need=5):
    """All five planners propose in parallel; duplicates/invalid are re-asked once (still model decisions)."""
    plans, n = [], 0
    def one(agent, avoid=None):
        parsed, r = call_agent(log, agent, f"{round_name}:propose", planner_messages(agent, mission, feed, note, avoid), plan_schema(feed), 0.9)
        return agent, parsed, r
    with cf.ThreadPoolExecutor(5) as ex:
        results = list(ex.map(lambda a: one(a), PERSONAS))
    seen = set()
    for agent, parsed, r in results:
        n += 1
        plans.append(_record_plan(log, mission, feed, round_name, agent, parsed, r, n, seen))
    # Duplicates / incomplete answers are re-asked (up to 3 rounds), telling the model every combination
    # already proposed. Each re-ask is a new, recorded model decision; nothing is filled in by code.
    for _ in range(3):
        distinct_complete = {p["key"] for p in plans if p["key"]}
        distinct_feasible = {p["key"] for p in plans if p["evaluation"]["feasible"]}
        if len(distinct_complete) >= need and len(distinct_feasible) >= 3:
            break
        avoid = [list(map(list, k)) for k in sorted(distinct_complete)]
        latest = {}
        for p in plans: latest[p["agent"]] = p
        retry = [a for a, p in latest.items() if p["duplicate"] or not p["key"] or not p["evaluation"]["feasible"]] or list(PERSONAS)[:2]
        with cf.ThreadPoolExecutor(5) as ex:
            again = list(ex.map(lambda a: one(a, avoid), retry))
        for agent, parsed, r in again:
            n += 1
            plans.append(_record_plan(log, mission, feed, round_name, agent, parsed, r, n, seen))
    return plans


def _record_plan(log, mission, feed, round_name, agent, parsed, r, n, seen):
    pid = f"{round_name.upper()}-P{n}"
    ev = catalog.evaluate_plan(parsed or {}, feed, mission["cap_cents"])
    key = catalog.plan_key(ev.get("normalized_lines") or []) if ev["valid"] else None
    dup = key in seen if key else False
    if key: seen.add(key)
    plan = {"plan_id": pid, "round": round_name, "agent": agent, "decider": "nim", "model": r.get("model"), "response_id": r.get("response_id"),
            "proposal": parsed, "evaluation": ev, "key": key, "duplicate": dup, "feed_version": feed["version"]}
    log["events"].emit("PLAN_PROPOSED", agent, plan_id=pid, round=round_name, total_cents=ev["total_cents"], feasible=ev["feasible"],
                       duplicate=dup, reasons=ev["reasons"], response_id=r.get("response_id"))
    log["activity"].emit("SCRIPTED_DECISION", "agent:budget-auditor", decider="scripted", label=SCRIPTED, checked_plan=pid,
                         feasible=ev["feasible"], reasons=ev["reasons"], total_cents=ev["total_cents"])
    return plan


def chair_select(log, mission, plans, round_name, note=None):
    candidates = [p for p in plans if p["evaluation"]["feasible"] and not p["duplicate"]]
    table = [{"plan_id": p["plan_id"], "proposed_by": p["agent"], "lines": p["evaluation"]["normalized_lines"],
              "total_cents": p["evaluation"]["total_cents"], "planner_rationale": (p["proposal"] or {}).get("rationale", "")[:600]} for p in candidates]
    infeasible = [{"plan_id": p["plan_id"], "reasons": p["evaluation"]["reasons"]} for p in plans if not p["evaluation"]["feasible"]]
    msgs = [{"role": "system", "content": "You are agent:chair of the LEGION procurement collective. Select exactly one plan from the "
             "feasible candidates (all were independently verified against current quotes). Prefer quality and delivery reliability, "
             "but keep budget headroom. Give a justification (at most 400 characters) and a one-sentence reason for rejecting every other candidate. "
             "If there are no feasible candidates, set selected_plan_id to NONE. Answer with compact single-line JSON only."},
            {"role": "user", "content": json.dumps({"mission": mission["statement"], "budget_cap_cents": mission["cap_cents"],
                                                    "feasible_candidates": table, "already_infeasible": infeasible, "situation": note})}]
    for attempt in range(2):
        parsed, r = call_agent(log, "agent:chair", f"{round_name}:select", msgs, CHAIR_SCHEMA, 0.3)
        chosen = next((p for p in candidates if parsed and p["plan_id"] == parsed.get("selected_plan_id")), None)
        if chosen:
            log["events"].emit("PLAN_SELECTED", "agent:chair", plan_id=chosen["plan_id"], round=round_name,
                               total_cents=chosen["evaluation"]["total_cents"], justification=parsed.get("justification"),
                               rejections=parsed.get("rejections"), response_id=r.get("response_id"))
            return chosen, parsed
        log["events"].emit("CHAIR_SELECTION_INVALID", "agent:chair", round=round_name, output=parsed, response_id=r.get("response_id"))
    raise RuntimeError("CHAIR_FAILED_TO_SELECT_A_FEASIBLE_PLAN")


def shock_for(plan, cap_cents=50000):
    """Preregistered disruption: the selected plan's highest-priced line rises so that the plan total exceeds the
    cap by 10% of that line's price (rounded up to whole dollars)."""
    import math
    top = max(plan["evaluation"]["normalized_lines"], key=lambda l: l["price_cents"])
    total = plan["evaluation"]["total_cents"]
    new = int(math.ceil((top["price_cents"] + (cap_cents - total) + top["price_cents"] * 0.10) / 100.0)) * 100
    return {"service_id": top["service_id"], "supplier_id": top["supplier_id"], "quote_id_before": top["quote_id"],
            "old_price_cents": top["price_cents"], "new_price_cents": new}


def run(log, mission, out):
    """Phases P1-P4 of the single attempt. Returns the final selected plan and the full trace."""
    reg = registry()
    log["events"].emit("COLLECTIVE_REGISTERED", "agent:coordinator", registered=100,
                       by_decider={d: sum(a["decider"] == d for a in reg) for d in ("nim", "scripted", "standby")})
    (out / "AGENT_REGISTRY.json").write_text(json.dumps(reg, indent=1) + "\n")
    f1 = catalog.feed(1)
    for i in (1, 2, 3):
        log["activity"].emit("SCRIPTED_DECISION", f"agent:quote-collector-{i}", decider="scripted", label=SCRIPTED,
                             read_feed_version=1, quotes=[q["quote_id"] for q in f1["quotes"] if q["service_id"] == f"SVC-{i}"])
    log["events"].emit("QUOTES_COLLECTED", "agent:coordinator", feed_version=1, feed_sha256=catalog.feed_digest(f1), simulated=True)
    plans1 = propose_round(log, mission, f1, "initial")
    sel1, chair1 = chair_select(log, mission, plans1, "initial")
    # ---- disruption (same attempt)
    shock = shock_for(sel1, mission["cap_cents"])
    f2 = catalog.feed(2, shock)
    shock_event = {"schema": "commerce.demo.price-shock.v1", "simulated": True, "injector": "preregistered disruption protocol",
                   "affected_plan": sel1["plan_id"], **shock, "feed_v1_sha256": catalog.feed_digest(f1), "feed_v2_sha256": catalog.feed_digest(f2),
                   "published_at": time.time()}
    log["events"].emit("PRICE_SHOCK", "disruption-injector", **shock_event)
    log["activity"].emit("SCRIPTED_DECISION", "agent:price-monitor", decider="scripted", label=SCRIPTED, read_feed_version=2,
                         detected_change={"quote": shock["quote_id_before"], "old": shock["old_price_cents"], "new": shock["new_price_cents"]})
    recheck = catalog.evaluate_plan({"lines": sel1["evaluation"]["normalized_lines"], "total_cents": None}, f2, mission["cap_cents"])
    new_total = recheck["total_cents"]
    log["events"].emit("PLAN_RECHECKED", "agent:price-monitor", plan_id=sel1["plan_id"], old_total_cents=sel1["evaluation"]["total_cents"],
                       new_total_cents=new_total, cap_cents=mission["cap_cents"], over_budget=new_total > mission["cap_cents"])
    replanned = new_total > mission["cap_cents"]
    if replanned:
        log["events"].emit("PLAN_REJECTED", "agent:budget-auditor", plan_id=sel1["plan_id"],
                           reason=f"OVER_BUDGET_AFTER_PRICE_SHOCK:{new_total}>{mission['cap_cents']}")
        note = (f"Price change: quote {shock['quote_id_before']} ({shock['supplier_id']}, {shock['service_id']}) rose from "
                f"{shock['old_price_cents']} to {shock['new_price_cents']} cents. The previously selected plan {sel1['plan_id']} now totals "
                f"{new_total} cents and exceeds the {mission['cap_cents']} cent budget, so it was rejected. Propose a new compliant plan "
                "for the same three obligations using the updated quotes.")
        plans2 = propose_round(log, mission, f2, "replan", note=note)
        final, chair2 = chair_select(log, mission, plans2, "replan", note=note)
    else:
        plans2, final, chair2 = [], sel1, chair1
    log["events"].emit("FINAL_PLAN", "agent:chair", plan_id=final["plan_id"], total_cents=final["evaluation"]["total_cents"],
                       lines=final["evaluation"]["normalized_lines"], feed_version=final["feed_version"], replanned=replanned)
    return {"plans_initial": plans1, "selected_initial": sel1["plan_id"], "chair_initial": chair1, "price_shock": shock_event,
            "recheck_after_shock": recheck, "replanned": replanned, "plans_replan": plans2, "chair_replan": chair2,
            "final": final, "feeds": {"v1": f1, "v2": f2}}
