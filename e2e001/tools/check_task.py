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
"""
import json
import sys

MAX_TURNS = 80
BASE_WIDGETS = {"bar1", "grid_offers", "pie_checks", "grid_events", "grid_checks"}


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
    accepted = lambda t: not (t["request"].get("x_adapter") or {}).get("turnId") or \
        t["request"]["x_adapter"]["attempt"] == last_attempt[t["request"]["x_adapter"]["turnId"]]
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
            if isinstance(parsed, dict) and "adapter_validation" in parsed:
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
        "final_answer_visible_in_chat": bool(final) and final.strip()[:60] in (chat.get("panelText") or ""),
    }
    detail = {"turns": len(turns), "executed_tool_results": len(executed), "adapter_rejections": len(adapter_rejections),
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
        if task.get("expect_dom"):
            checks["dom_shows_expected"] = all(tok in [x.strip() for x in (chat.get("dom_widget_text") or "").split("|")] for tok in task["expect_dom"])
    result = {"task": tid, "prompt": task["prompt"], "pass": all(checks.values()), "checks": checks, "detail": detail}
    json.dump(result, open(out_path, "w", encoding="utf-8"), indent=1)
    print(json.dumps({"task": tid, "pass": result["pass"], "checks": checks, "turns": len(turns),
                      "adapter_rejections": len(adapter_rejections), "final": final[:200]}))


if __name__ == "__main__":
    main()
