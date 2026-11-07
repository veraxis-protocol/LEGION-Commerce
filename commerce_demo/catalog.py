"""DEMO-006 supplier catalog. SIMULATED sandbox fixture data, not real supplier negotiations.

Three service obligations, four simulated suppliers each. Prices are integer cents. The feed is
versioned; the preregistered disruption publishes version 2 with one price increase.
"""
import copy
import hashlib
import json

SIMULATED = "SIMULATED_SANDBOX_SUPPLIER_FEED - fixture data, no real supplier was contacted"

OBLIGATIONS = [
    {"id": "SVC-1", "service": "Website accessibility audit (WCAG 2.2 AA, 10 pages) with written findings report",
     "requirement": "supplier rating >= 4.0 and delivery <= 10 days"},
    {"id": "SVC-2", "service": "Professional proofreading of website copy (up to 5,000 words) with tracked changes",
     "requirement": "supplier rating >= 4.0 and delivery <= 10 days"},
    {"id": "SVC-3", "service": "Cloud security configuration review (one AWS account) with written remediation checklist",
     "requirement": "supplier rating >= 4.0 and delivery <= 10 days"},
]
MIN_RATING_X10 = 40
MAX_DELIVERY_DAYS = 10

_FEED_V1 = {
    "SVC-1": [("SUP-A1", "Northwind Access (simulated)", 14500, 46, 5), ("SUP-A2", "Clearpath A11y (simulated)", 12900, 42, 7),
              ("SUP-A3", "Lumen Audit Co (simulated)", 17900, 48, 3), ("SUP-A4", "QuickCheck Labs (simulated)", 9900, 36, 10)],
    "SVC-2": [("SUP-B1", "Redline Editorial (simulated)", 11900, 47, 2), ("SUP-B2", "Plain Words (simulated)", 8900, 41, 4),
              ("SUP-B3", "Stet & Co (simulated)", 14900, 49, 1), ("SUP-B4", "BudgetProof (simulated)", 6500, 34, 6)],
    "SVC-3": [("SUP-C1", "Bastion Advisory (simulated)", 18900, 48, 4), ("SUP-C2", "Cumulus Secure (simulated)", 15500, 44, 5),
              ("SUP-C3", "Ironclad Cloud (simulated)", 21900, 49, 2), ("SUP-C4", "Patchwise (simulated)", 11900, 38, 8)],
}


def feed(version=1, shock=None):
    quotes = []
    for svc, rows in _FEED_V1.items():
        for sid, name, cents, rating, days in rows:
            quotes.append({"quote_id": f"Q-{svc}-{sid}-v{version}", "service_id": svc, "supplier_id": sid, "supplier": name,
                           "price_cents": cents, "rating_x10": rating, "delivery_days": days, "feed_version": version,
                           "source": SIMULATED})
    if shock:
        for q in quotes:
            if q["service_id"] == shock["service_id"] and q["supplier_id"] == shock["supplier_id"]:
                q["price_cents"] = shock["new_price_cents"]
    return {"schema": "commerce.demo.supplier-feed.v1", "version": version, "simulated": True, "notice": SIMULATED, "quotes": quotes}


def feed_digest(f):
    return hashlib.sha256(json.dumps(f, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def evaluate_plan(plan, current_feed, cap_cents):
    """Deterministic, independent check of a proposed plan against the CURRENT feed and mission constraints."""
    reasons = []
    lines = plan.get("lines") if isinstance(plan, dict) else None
    if not isinstance(lines, list):
        return {"valid": False, "feasible": False, "reasons": ["NO_LINES"], "total_cents": None}
    by_q = {q["quote_id"]: q for q in current_feed["quotes"]}
    by_sup = {(q["service_id"], q["supplier_id"]): q for q in current_feed["quotes"]}
    services = [l.get("service_id") for l in lines]
    if sorted(services) != ["SVC-1", "SVC-2", "SVC-3"]:
        reasons.append("OBLIGATIONS_NOT_EXACTLY_THREE_ORIGINAL")
    total = 0
    norm = []
    for l in lines:
        q = by_sup.get((l.get("service_id"), l.get("supplier_id")))
        if q is None:
            reasons.append(f"UNKNOWN_SUPPLIER:{l.get('service_id')}:{l.get('supplier_id')}"); continue
        if l.get("price_cents") != q["price_cents"]:
            reasons.append(f"PRICE_NOT_CURRENT_QUOTE:{q['quote_id']}:{l.get('price_cents')}!={q['price_cents']}")
        if q["rating_x10"] < MIN_RATING_X10:
            reasons.append(f"RATING_BELOW_REQUIREMENT:{q['supplier_id']}")
        if q["delivery_days"] > MAX_DELIVERY_DAYS:
            reasons.append(f"DELIVERY_TOO_SLOW:{q['supplier_id']}")
        total += q["price_cents"]
        norm.append({"service_id": q["service_id"], "supplier_id": q["supplier_id"], "supplier": q["supplier"],
                     "quote_id": q["quote_id"], "price_cents": q["price_cents"]})
    if plan.get("total_cents") != total:
        reasons.append(f"STATED_TOTAL_WRONG:{plan.get('total_cents')}!={total}")
    if total > cap_cents:
        reasons.append(f"OVER_BUDGET:{total}>{cap_cents}")
    structural = not any(r.startswith(("OBLIGATIONS", "UNKNOWN", "RATING", "DELIVERY")) for r in reasons)
    return {"valid": structural, "feasible": structural and total <= cap_cents and not any(r.startswith("PRICE_NOT_CURRENT") for r in reasons),
            "reasons": reasons, "total_cents": total, "normalized_lines": sorted(norm, key=lambda x: x["service_id"])}


def plan_key(lines):
    return tuple(sorted((l["service_id"], l["supplier_id"]) for l in lines))
