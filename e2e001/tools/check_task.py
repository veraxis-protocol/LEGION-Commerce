"""WO-COM-E2E-002 deterministic task checker (read-only; evidence only).

  python tools/check_task.py <TASKS.json> <task_id> <journal.jsonl> <CHAT_RESULT.json> <out.json>

PASS requires ALL of:
  * completion: the conversation went idle within the time limit, with <= MAX_TURNS backend calls (no loop);
  * zero schema violations reaching AG Studio: no AG tool result is a validation failure;
  * a final assistant text answer exists (journal: last lead-agent reply is a message);
  * answer tasks: the final answer contains every expected token AND every grounding token appears in a tool result
    that AG Studio actually executed (so an answer the model never read from the data cannot pass);
  * widget tasks: a NEW widget of an allowed type exists in the Studio state with the required mapping, and AG Studio's
    own health check reports no data_mapping_complete / data_present issue for it (it renders data).
Adapter rejections (model calls that failed validation and were never executed) are counted and reported, not hidden.

WO-COM-E2E-003 additions (stricter only; nothing above is relaxed):
  * outputs carrying "adapter_gate" (completions rejected by the adapter gate, never executed) count as adapter rejections;
  * widget tasks with a "predicate": the qualifying widget must hold that predicate in its STORED filters
    (pages[].filter.widget[id] or page filters, from getState), AND its rendered value (read from the DOM by chat.mjs)
    must equal the ground truth this checker computes itself from the dataset captured in CHAT_RESULT.json.
    A correct number without the predicate fails (NO-GO condition of E2E-003).
"""
import json
import os
import re
import sys

MAX_TURNS = 80
BASE_WIDGETS = {"bar1", "grid_offers", "pie_checks", "grid_events", "grid_checks"}


def md_plain(s):
    s = re.sub(r"(\*\*|__|`)", "", s)
    s = re.sub(r"(?m)^\s*(#{1,6}\s+|[-*+]\s+|\d+\.\s+)", "", s)
    return re.sub(r"\s+", " ", s).strip()


def main():
    tasks_path, tid, journal, chat_path, out_path = sys.argv[1:6]
    task = next(t for t in json.load(open(tasks_path))["tasks"] if t["id"] == tid)
    turns = [json.loads(l) for l in open(journal, encoding="utf-8")]
    chat = json.load(open(chat_path, encoding="utf-8"))
    executed, validation_failures, adapter_rejections, texts = [], [], [], []
    seen_outputs = set()
    # Only replies the adapter ACCEPTED reach AG Studio / the user: a reply is superseded when a later backend call carries
    # the same x_adapter.turnId with a higher attempt (checker fix after nim_T2_a1, whose CHECK.json is preserved).
    last_attempt = {}
    for t in turns:
        xa = t["request"].get("x_adapter") or {}
        if xa.get("turnId"):
            last_attempt[xa["turnId"]] = max(last_attempt.get(xa["turnId"], 0), xa.get("attempt", 0))
    # E2E-003: a reply the adapter's completion gate rejected was never delivered (journal: <session>.gate.jsonl).
    gate_path = journal[:-len(".jsonl")] + ".gate.jsonl"
    gate = [json.loads(l) for l in open(gate_path, encoding="utf-8")] if os.path.exists(gate_path) else []
    gate_rejected = {(g["turnId"], g["attempt"]) for g in gate if not g["pass"]}
    xa_of = lambda t: t["request"].get("x_adapter") or {}
    accepted = lambda t: (not xa_of(t).get("turnId") or xa_of(t)["attempt"] == last_attempt[xa_of(t)["turnId"]]) and \
        (xa_of(t).get("turnId"), xa_of(t).get("attempt")) not in gate_rejected
    for t in turns:
        for it in t["request"].get("input", []):
            if it.get("type") != "function_call_output" or it["callId"] in seen_outputs:
                continue
            seen_outputs.add(it["callId"])
            out = it.get("output", "")
            try:
                parsed = json.loads(out)
            except ValueError:
                parsed = {"raw": out}
            if isinstance(parsed, dict) and ("adapter_validation" in parsed or "adapter_gate" in parsed):
                adapter_rejections.append(parsed.get("issues"))
                continue
            executed.append(out)
            issues = json.dumps(parsed.get("issues", "")) if isinstance(parsed, dict) else ""
            if isinstance(parsed, dict) and parsed.get("success") is False and "validation" in issues:
                validation_failures.append(parsed["issues"][:3])
        for o in t["response"].get("output", []) if accepted(t) else []:
            if o["type"] == "message":
                texts.append("".join(c.get("text", "") for c in o["content"]))
    final = texts[-1] if texts else ""
    checks = {
        "completed_idle": chat["outcome"] == "IDLE",
        "no_loop": len(turns) <= MAX_TURNS,
        "zero_schema_violations_at_ag": not validation_failures,
        "final_answer_present": bool(final) and not final.startswith("ADAPTER:"),
        # E2E-003 v3 (frozen before run nimC): the chat panel renders markdown, so "**microcenter.com**" is displayed as
        # "microcenter.com" (nimB_T3). Both sides are compared after removing markdown markup and collapsing whitespace.
        # Answer correctness and grounding still use the raw final text; nothing else changed.
        "final_answer_visible_in_chat": bool(final) and md_plain(final)[:60] in md_plain(chat.get("panelText") or ""),
    }
    if not final and "ADAPTER: task NOT completed" in (chat.get("panelText") or ""):
        final = "ADAPTER: task NOT completed (gate)"  # the adapter replaced the final answer; the model's text was never shown
    detail = {"turns": len(turns), "gate_decisions": [{"kind": g["kind"], "attempt": g["attempt"], "pass": g["pass"]} for g in gate], "executed_tool_results": len(executed), "adapter_rejections": len(adapter_rejections),
              "ag_validation_failures": validation_failures[:5], "final_answer": final[:800]}
    if task["kind"] == "answer":
        low = final.lower()
        checks["answer_correct"] = all(tok.lower() in low for tok in task["expect_all"])
        checks["answer_grounded_in_executed_tool_result"] = all(any(g.lower() in e.lower() for e in executed) for g in task["grounding"])
    else:
        state = (chat.get("studio") or {}).get("state") or {"pages": []}
        health = (chat.get("studio") or {}).get("health") or {}
        new = {wid: w for p in state["pages"] for wid, w in (p.get("widgets") or {}).items() if wid not in BASE_WIDGETS}
        ok = []
        for wid, w in new.items():
            m = json.dumps(w.get("dataMapping", {}))
            mp = task["mapping"]
            type_ok = w.get("type") in task["widget_types"]
            if "category" in mp:
                map_ok = (mp["category"] in m and mp["value"] in m and f'"aggregation": "{mp["aggregation"]}"' in m)
            else:
                map_ok = f'"{mp["source"]}.' in m
            bad_health = [i for i in health.get(wid, []) if i.get("code") in ("data_mapping_complete", "data_present") and (i.get("score") or 0) < 1]
            ok.append({"widget": wid, "type": w.get("type"), "type_ok": type_ok, "mapping_ok": map_ok, "health_issues": bad_health, "dataMapping": w.get("dataMapping")})
        detail["new_widgets"] = ok
        checks["widget_created_with_correct_mapping"] = any(x["type_ok"] and x["mapping_ok"] for x in ok)
        checks["widget_renders_data"] = any(x["type_ok"] and x["mapping_ok"] and not x["health_issues"] for x in ok)
        if task.get("predicate"):
            pr = task["predicate"]
            col = pr["field"].split(".", 1)[1]
            truth = sum(1 for r in (chat.get("dataset_checks") or []) if r.get(col) == pr["value"])
            detail["ground_truth_count"] = truth
            rendered = (chat.get("studio") or {}).get("rendered") or {}
            ev = []
            for x in ok:
                stored = []
                for p in state["pages"]:
                    if x["widget"] in (p.get("widgets") or {}):
                        flt = p.get("filter") or {}
                        stored = list((flt.get("widget") or {}).get(x["widget"]) or []) + list(flt.get("page") or [])
                has_pred = any((f.get("field") or {}).get("id") == pr["field"] and (
                    ((f.get("model") or {}).get("operator") == "equals" and f["model"].get("value") == pr["value"]) or
                    ((f.get("model") or {}).get("operator") == "isIn" and f["model"].get("value") == [pr["value"]])) for f in stored)
                vt = (rendered.get(x["widget"]) or {}).get("value_text")
                try:
                    shown = float(str(vt).replace(",", ""))
                except (TypeError, ValueError):
                    shown = None
                ev.append({"widget": x["widget"], "qualifies": x["type_ok"] and x["mapping_ok"], "stored_filters": stored,
                           "predicate_present": has_pred, "rendered_value": vt, "matches_ground_truth": shown == truth})
            detail["predicate_evidence"] = ev
            good = [e for e in ev if e["qualifies"]]
            checks["predicate_in_widget_state"] = any(e["predicate_present"] for e in good)
            checks["displayed_value_matches_ground_truth"] = any(e["predicate_present"] and e["matches_ground_truth"] for e in good)
        if task.get("expect_dom"):
            checks["dom_shows_expected"] = all(tok in [x.strip() for x in (chat.get("dom_widget_text") or "").split("|")] for tok in task["expect_dom"])
    result = {"task": tid, "prompt": task["prompt"], "pass": all(checks.values()), "checks": checks, "detail": detail}
    json.dump(result, open(out_path, "w", encoding="utf-8"), indent=1)
    print(json.dumps({"task": tid, "pass": result["pass"], "checks": checks, "turns": len(turns),
                      "adapter_rejections": len(adapter_rejections), "final": final[:200]}))


if __name__ == "__main__":
    main()
