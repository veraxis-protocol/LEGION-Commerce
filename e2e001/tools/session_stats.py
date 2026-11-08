"""WO-COM-E2E-003: per-session traces/cost summary from the journals (read-only).
  python tools/session_stats.py <out.json> <session> [<session> ...]
Counts backend calls, model calls (non-script), adapter rejections by kind, gate decisions, NIM tokens (from the raw
provider responses), transport retries, latency, distinct models. NIM free-endpoint calls carry no per-call price; tokens
are the cost measure."""
import json, os, sys, collections
out, sessions = sys.argv[1], sys.argv[2:]
res = {}
for s in sessions:
    j = f"ai_turns/{s}.jsonl"
    turns = [json.loads(l) for l in open(j, encoding="utf-8")]
    rej = collections.Counter()
    for t in turns:
        for h in ((t["request"].get("x_adapter") or {}).get("previous") or [])[-1:]:
            rej[h.get("rejected")] += 1
    tok = collections.Counter(); lat = 0.0; retries = 0; models = set(); tools = collections.Counter()
    seen = set()
    for t in turns:
        u = ((t.get("raw_provider_response") or {}).get("usage")) or {}
        tok["prompt"] += u.get("prompt_tokens") or 0; tok["completion"] += u.get("completion_tokens") or 0
        m = t.get("meta") or {}
        lat += m.get("latency_s") or 0; retries += len(m.get("transport_retries") or [])
        if t.get("model"): models.add(t["model"])
        for it in t["request"].get("input", []):
            if it.get("type") == "function_call" and it["callId"] not in seen:
                seen.add(it["callId"]); tools[it["name"]] += 1
    gp = j[:-6] + ".gate.jsonl"
    gate = [json.loads(l) for l in open(gp, encoding="utf-8")] if os.path.exists(gp) else []
    res[s] = {"backend_calls": len(turns), "models": sorted(models), "tokens": dict(tok, total=tok["prompt"] + tok["completion"]),
              "model_latency_s": round(lat, 1), "transport_retries": retries, "adapter_rejections_by_kind": dict(rej),
              "gate_decisions": [{"kind": g["kind"], "attempt": g["attempt"], "pass": g["pass"]} for g in gate],
              "tool_calls_executed_by_name": dict(tools)}
json.dump(res, open(out, "w"), indent=1)
for s, r in res.items():
    print(s, r["backend_calls"], r["tokens"]["total"], r["adapter_rejections_by_kind"], len(r["gate_decisions"]))
