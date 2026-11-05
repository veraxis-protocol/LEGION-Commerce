"""Synthetic signatures and provider responses for MECHANISM_ONLY tests.

Never writes to LOCALAPPDATA and never imports a live credential-owning writer.
"""
import base64
import copy
import hashlib
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from commerce_adapter.contracts import Admission, canonical, digest
from commerce_adapter.gateway import Gateway, ProviderError


class Fixture:
    def __init__(self, root, amounts=(100,)):
        self.root = Path(root)
        self.keys = {n: Ed25519PrivateKey.generate() for n in ("control", "admission", "acceptance")}
        self.trust = {n: base64.b64encode(k.public_key().public_bytes_raw()).decode() for n,k in self.keys.items()}
        names = ["PROOF_MAP.json", "LEAN_BUILD.txt", "AXIOM_AUDIT.txt", "FORMAL_INPUTS.jsonl", "LEAN_VERDICTS.jsonl",
                 "RUNTIME_VERDICTS.jsonl", "MISMATCHES.jsonl", "PERMIT_TRACE.jsonl", "NEGATIVE_RESULTS.json"]
        for n in names:
            (self.root / n).write_text("SYNTHETIC TEST FIXTURE; NOT A03 EVIDENCE", encoding="utf-8")
        self.acceptance = {"schema":"commerce.a03.acceptance.v1", "status":"PASS", "tdd_sha256":"TEST_ONLY",
                           "source_pins":{"fixture":"MOCK_ONLY"}, "expires_at":2000,
                           "tests":{f"A03-T{i:02}":"PASS" for i in range(1,14)},
                           "evidence_hashes":{n:hashlib.sha256((self.root/n).read_bytes()).hexdigest() for n in names}}
        self.acceptance["adapter_schema"]="commerce.a04.adapter.v1"
        self.acceptance["runtime_source_hashes"]={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
             for p in (Path(__file__).resolve().parent.parent/"commerce_adapter").glob("*.py")}
        self.acceptance_envelope = self.sign("acceptance", "commerce.a03.acceptance.v1", self.acceptance)
        self.now = 1000
        self.admission = Admission(self.trust, self.acceptance_envelope, self.root, "TEST_ONLY", {"fixture":"MOCK_ONLY"}, lambda:self.now)
        intents = [{"obligation_id":f"service-{i}", "effect_slot":"bundle", "actor":"test-agent", "merchant_id":"MERCHANT1",
                    "quote_hash":"test-quote", "prerequisites_hash":"test-prerequisites", "currency":"USD", "amount_cents":a} for i,a in enumerate(amounts)]
        self.mission = {"schema":"commerce.mission.v1", "mission_id":"TEST-MISSION", "contract_version":"1",
                        "contract_hash":"test-contract", "epoch":1, "actor":"test-agent", "deadline":1800,
                        "currency":"USD", "cap_cents":50000, "obligations":[i["obligation_id"] for i in intents],
                        "merchant_ids":["MERCHANT1"], "effects":intents}
        self.permits = [{"schema":"commerce.permit.v1", "permit_id":f"TEST-PERMIT-{i}", "issued_at":900, "expires_at":1400,
                         "quote_expires_at":1500, "mission_hash":digest("commerce.mission.v1", self.mission),
                         "acceptance_hash":digest("commerce.a03.acceptance.v1", self.acceptance), "epoch":1,
                         "lean_rule_earned":True, "controller_act":True, "cage_allow":True, "veip_eligible":True,
                         "prerequisites_satisfied":True, "verdict":"T", "scope":"paypal.sandbox.orders.create_capture", "intent":copy.deepcopy(v)} for i,v in enumerate(intents)]
        self.control = {"schema":"commerce.control.v1", "sequence":1, "mission_id":"TEST-MISSION", "epoch":1,
                        "mission_hash":digest("commerce.mission.v1", self.mission), "active":True,
                        "expires_at":1800, "revoked_permits":[]}
        self.provider = FakePayPal()
        self.gateway = Gateway(self.root / "ledger.db", self.admission, self.provider)
        self.apply()

    def sign(self, key, domain, payload):
        return {"payload":copy.deepcopy(payload), "signature":base64.b64encode(self.keys[key].sign(domain.encode()+b"\x00"+canonical(payload))).decode()}

    def apply(self):
        self.gateway.apply_control("TEST-MISSION", self.sign("control", "commerce.control.v1", self.control))

    def envelopes(self, index=0):
        return self.sign("control", "commerce.mission.v1", self.mission), self.sign("admission", "commerce.permit.v1", self.permits[index])

    def reserve(self, index=0):
        return self.gateway.reserve(*self.envelopes(index))

    def create(self):
        eid = self.reserve()
        self.gateway.create(eid)
        return eid


class FakePayPal:
    evidence_mode = "MOCK_ONLY"
    def __init__(self):
        self.calls = []
        self.orders = {}
        self.details = {}
        self.next_id = 1
        self.error = None
        self.lost_create = False
        self.lost_capture = False
        self.pending = False
        self.declined = False
        self.tamper = None

    def request(self, method, path, body=None, request_id=None):
        self.calls.append((method,path,request_id))
        if self.error:
            raise ProviderError(*self.error)
        if method == "POST" and path == "/v2/checkout/orders":
            oid = "ORDER" + str(self.next_id)
            self.next_id += 1
            self.orders[oid] = {"id":oid, "intent":"CAPTURE", "status":"CREATED", "purchase_units":copy.deepcopy(body["purchase_units"]),
                                "links":[{"rel":"payer-action", "href":f"https://www.sandbox.paypal.com/checkoutnow?token={oid}"}]}
            if self.lost_create:
                raise ProviderError()
            return 201, copy.deepcopy(self.orders[oid])
        if method == "POST" and path.endswith("/capture"):
            oid = path.split("/")[-2]
            order = self.orders[oid]
            if order["status"] != "APPROVED":
                raise ProviderError(422,"HTTP_REJECTED")
            cid = "CAPTURE" + oid[5:]
            status = "PENDING" if self.pending else "DECLINED" if self.declined else "COMPLETED"
            c = {"id":cid, "status":status, "amount":copy.deepcopy(order["purchase_units"][0]["amount"])}
            order["purchase_units"][0]["payments"] = {"captures":[c]}
            order["status"] = "COMPLETED" if status == "COMPLETED" else "APPROVED"
            self.details[cid] = {**copy.deepcopy(c), "supplementary_data":{"related_ids":{"order_id":oid}}}
            if self.lost_capture:
                raise ProviderError()
            return 201, copy.deepcopy(order)
        if method == "GET":
            oid = path.split("/")[-1]
            result = copy.deepcopy(self.details[oid] if "/captures/" in path else self.orders[oid])
            if self.tamper:
                self.tamper(path, result)
            return 200, result
        raise AssertionError("Unexpected mock request")

    def approve(self):
        for order in self.orders.values():
            order["status"] = "APPROVED"
            order["payer"] = {"payer_id":"TESTBUYER"}

    def capture_count(self):
        return sum(m == "POST" and p.endswith("/capture") for m,p,_ in self.calls)
