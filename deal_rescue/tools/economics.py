"""ECONOMIC_CASE.json + HYPOTHETICAL_BREAK_EVEN.json for a built dossier (integer cents, self-tested).

NetContributionCents = buyer_receipts - supplier - engineering - shipping - paypal_fees - inference - verification - support - expected_loss
Unknown components stay "Z" and make the net an upper bound, never a filled-in zero.
PayPal fee is an ASSUMPTION taken from PayPal's published US merchant fee page (standard PayPal Checkout
3.49% + $0.49 per transaction); it was not re-verified against a merchant account and Sandbox charges no fees.
Usage: python -B deal_rescue/tools/economics.py <dossier.json> <inference_json> <out_dir>
"""
import json
import math
import sys
from pathlib import Path

FEE_PCT_BP = 349      # 3.49 % in basis points
FEE_FIXED = 49        # cents
FEE_SOURCE = "https://www.paypal.com/us/business/paypal-business-fees (PayPal Checkout, US, 3.49% + $0.49) — ASSUMPTION, not merchant-verified"


def fee(c):
    return math.ceil(c * FEE_PCT_BP / 10000) + FEE_FIXED


def main(dossier, inference, out):
    d = json.loads(Path(dossier).read_text())
    inf = json.loads(Path(inference).read_text())
    v3 = d["terms_versions"][-1]
    n = len(d["requests"])
    price = v3["per_buyer_cents"]
    receipts = price * n
    fees = sum(fee(price) for _ in range(n))
    known = {"buyer_receipts": receipts, "supplier_costs": v3["factory_cents"], "engineering_costs": v3["qualification_cents"],
             "shipping_costs": v3["shipping_cents"], "paypal_fees_assumed": fees}
    contribution = receipts - v3["total_cents"]
    upper_bound = contribution - fees
    case = {
        "schema": "legion.deal-rescue.economic-case.v1", "run_id": d["run_id"], "evidence_mode": d["evidence_mode"],
        "perspective": "merchant_of_record candidate (pass-through), synthetic fixture prices",
        "formula": "NetContributionCents = buyer_receipts - supplier_costs - engineering_costs - shipping_costs - PayPal_fees - inference_cost - verification_cost - support_cost - expected_loss",
        "known_cents": known,
        "paypal_fee_assumption": {"per_transaction_cents": fee(price), "basis": FEE_SOURCE, "transactions": n},
        "unknown": {"inference_cost": {"status": "Z", "measured": {k: inf[k] for k in ("model_calls", "prompt_tokens", "completion_tokens")}, "price": "NIM trial endpoint, no invoice"},
                    "verification_cost": "Z", "support_cost": "Z", "expected_loss": "Z", "taxes": "Z (not modelled)", "refund_risk": "Z"},
        "gross_contribution_before_overhead_cents": contribution,
        "net_contribution_upper_bound_cents": upper_bound,
        "net_contribution_cents": "Z (upper bound %d; all unknown terms are >= 0)" % upper_bound,
        "verdict": "NEGATIVE ECONOMICS under the published-fee assumption" if upper_bound < 0 else "NOT ECONOMICALLY VALIDATED",
        "Commercial_GO": False,
        "Commercial_GO_terms": {"NetContribution>0": "F" if upper_bound <= 0 else "Z", "full_commitments_valid": "T (synthetic)",
                                "merchant_of_record_identified": "F", "purchase_allowed": "F (PayPal admission F)", "independent_acceptance_possible": "T (synthetic Judge)"},
        "metrics": {"RescueRate": f"{n}/{n} synthetic (accepted rescued / originally unfulfillable, fixture only)", "PositiveNetDeals": 0,
                    "ConfirmedSandboxTPV_cents": 0, "RealIncrementalTPV": "Z"},
    }
    # hypothetical sensitivity (NOT a scenario run, NOT reusing the Judge verdict)
    cap = min(r["max_cents"] for r in d["requests"])
    be_price = next((p for p in range(price, 100000) if p * n - v3["total_cents"] - n * fee(p) > 0), None)
    max_cost = cap * n - n * fee(cap)
    hyp = {"schema": "legion.deal-rescue.hypothetical-break-even.v1", "label": "HYPOTHETICAL — counterfactual sensitivity, no run, no Judge, no buyer, no supplier",
           "assumptions": {"paypal_fee": FEE_SOURCE, "inference_verification_support_loss": 0},
           "break_even_price_per_buyer_cents_at_frozen_costs": be_price,
           "exceeds_each_buyer_cap": be_price is not None and be_price > cap,
           "max_total_vendor_cost_for_break_even_at_caps_cents": max_cost - 1,
           "frozen_vendor_cost_cents": v3["total_cents"],
           "conclusion": "Under the frozen $100 caps the fixture cannot break even even with zero AI/verification/support cost; "
                         "a profitable rescue needs buyers whose willingness to pay exceeds vendor cost + fees, or vendor costs <= %d cents. "
                         "This is a requirement for a REAL opportunity, not a claim." % (max_cost - 1)}
    # self-tests
    assert receipts == 49500 and v3["total_cents"] == 49500 and contribution == 0, "frozen arithmetic changed"
    assert all(isinstance(v, int) for v in known.values())
    assert fee(9900) == 395 and fees == 1975
    assert case["Commercial_GO"] is False and upper_bound < 0
    assert hyp["exceeds_each_buyer_cap"] is True
    out = Path(out)
    (out / "ECONOMIC_CASE.json").write_text(json.dumps(case, indent=1), newline="\n")
    (out / "HYPOTHETICAL_BREAK_EVEN.json").write_text(json.dumps(hyp, indent=1), newline="\n")
    print(json.dumps({"contribution": contribution, "fees": fees, "upper_bound": upper_bound, "break_even_price": be_price, "self_tests": "PASS"}))


if __name__ == "__main__":
    main(*sys.argv[1:4])
