import concurrent.futures
import copy
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from commerce_adapter.contracts import Denied, cents, digest
from commerce_adapter.gateway import Gateway
from commerce_adapter.readback import independent_readback
from .helpers import Fixture

COLLECTED = []


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.f = Fixture(self.tmp.name,(32000,26000) if self._testMethodName.startswith("test_t03_") else (100,))

    def tearDown(self):
        db = sqlite3.connect(self.f.gateway.path)
        db.row_factory = sqlite3.Row
        events = [dict(r) for r in db.execute("SELECT * FROM events ORDER BY seq")]
        effects = [dict(r) for r in db.execute("SELECT id,amount,held,state,create_request_id,capture_request_id,order_id,capture_id FROM effects")]
        db.close()
        COLLECTED.append({"test":self.id(), "evidence_mode":"MOCK_ONLY", "events":events,
                          "effects":effects, "provider_calls":self.f.provider.calls})
        self.tmp.cleanup()

    def test_t01_approved_capture_requires_two_gets(self):
        """A04-T01 mechanism only; A05 no mission closure from payment."""
        eid = self.f.create()
        self.f.provider.approve()
        self.assertEqual(self.f.gateway.capture(eid), "CAPTURE_CONFIRMED")
        self.assertEqual(self.f.provider.capture_count(),1)
        result = independent_readback(self.f.gateway.path,eid,self.f.admission,self.f.provider)
        self.assertEqual(result["payment_status"],"VERIFIED_COMPLETED")
        self.assertFalse(result["mission_complete"])
        self.assertEqual(result["source"],"MOCK_ONLY")
        self.assertTrue(self.f.provider.calls[-1][1].startswith("/v2/payments/captures/"))

    def test_t02_denied_no_calls(self):
        """A04-T02 all independent authority gates, including UNKNOWN."""
        for gate in ("lean_rule_earned","controller_act","cage_allow","veip_eligible","prerequisites_satisfied"):
            with self.subTest(gate=gate):
                self.f.permits[0][gate] = False
                with self.assertRaises(Denied): self.f.reserve()
                self.f.permits[0][gate] = True
        for v in ("Z","F","UNKNOWN"):
            self.f.permits[0]["verdict"] = v
            with self.assertRaises(Denied): self.f.reserve()
        self.assertEqual(self.f.provider.calls,[])

    def test_t03_parallel_320_260(self):
        """A04-T03 actual SQLite concurrent writer contention (single host)."""
        def reserve(i):
            try: return self.f.reserve(i)
            except Denied: return None
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            values = list(pool.map(reserve,[0,1]))
        self.assertEqual(sum(v is not None for v in values),1)
        with closing(sqlite3.connect(self.f.gateway.path)) as db:
            self.assertLessEqual(db.execute("SELECT SUM(amount) FROM effects WHERE held=1").fetchone()[0],50000)

    def test_t04_same_effect_retry_after_timeout(self):
        """A04-T04 timeout retains identity and does not blindly retry create."""
        self.f.provider.lost_create=True
        eid=self.f.create()
        self.assertEqual(self.f.reserve(),eid)
        self.f.gateway.create(eid)
        self.assertEqual(len(self.f.provider.calls),1)
        self.assertEqual(self.f.gateway.status(eid)["state"],"RECONCILIATION_PENDING")
        self.assertEqual(self.f.gateway.status(eid)["held"],1)

    def test_t05_lost_success_reconcile_no_second_capture(self):
        """A04-T05 mock lost reply after success; live required separately."""
        eid=self.f.create(); self.f.provider.approve(); self.f.provider.lost_capture=True
        self.assertEqual(self.f.gateway.capture(eid),"RECONCILIATION_PENDING")
        self.assertEqual(self.f.gateway.reconcile(eid),"CAPTURE_CONFIRMED")
        self.f.gateway.capture(eid)
        self.assertEqual(self.f.provider.capture_count(),1)

    def test_t06_created_is_not_paid(self):
        """A04-T06 no buyer approval: no capture POST."""
        eid=self.f.create()
        self.assertEqual(self.f.gateway.capture(eid),"APPROVAL_PENDING")
        self.assertEqual(self.f.provider.capture_count(),0)
        with self.assertRaises(Denied): independent_readback(self.f.gateway.path,eid,self.f.admission,self.f.provider)

    def test_t07_pending(self):
        """A04-T07 PENDING holds reservation, never confirms."""
        eid=self.f.create(); self.f.provider.approve(); self.f.provider.pending=True
        self.assertEqual(self.f.gateway.capture(eid),"RECONCILIATION_PENDING")
        self.assertEqual(self.f.gateway.status(eid)["held"],1)
        self.f.gateway.capture(eid)
        self.assertEqual(self.f.provider.capture_count(),1)
        with self.assertRaises(Denied): independent_readback(self.f.gateway.path,eid,self.f.admission,self.f.provider)

    def test_t07_declined(self):
        """A04-T07 DECLINED never counts completed."""
        eid=self.f.create(); self.f.provider.approve(); self.f.provider.declined=True
        self.assertEqual(self.f.gateway.capture(eid),"FAILED_HELD")
        self.assertEqual(self.f.gateway.status(eid)["held"],1)
        self.f.gateway.capture(eid)
        self.assertEqual(self.f.provider.capture_count(),1)

    def test_t08_unsigned_changed_amount_merchant(self):
        """A04-T08 tampering rejected before any network."""
        for key,value in (("amount_cents",200),("merchant_id","OTHER")):
            mission,permit=self.f.envelopes()
            permit["payload"]["intent"][key]=value
            with self.assertRaises(Denied): self.f.gateway.reserve(mission,permit)
            self.f.permits[0]["intent"][key]=value
            # Authorized signer still cannot expand the owner's frozen mission.
            m,p=self.f.envelopes()
            p["payload"]["intent"][key]=value
            p=self.f.sign("admission","commerce.permit.v1",p["payload"])
            with self.assertRaises(Denied): self.f.gateway.reserve(m,p)
            self.f.permits[0]["intent"][key]=self.f.mission["effects"][0][key]
        self.assertEqual(self.f.provider.calls,[])

    def test_t09_revoke_after_reserve(self):
        """A04-T09 revocation committed before capture => zero further calls."""
        eid=self.f.create(); self.f.provider.approve()
        self.f.control["sequence"]+=1
        self.f.control["revoked_permits"]=["TEST-PERMIT-0"]
        self.f.apply(); previous=len(self.f.provider.calls)
        with self.assertRaises(Denied): self.f.gateway.capture(eid)
        self.assertEqual(len(self.f.provider.calls),previous)

    def test_t10_same_permit_concurrent(self):
        """A04-T10 idempotent same-effect join; exactly one create/capture."""
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            eids=list(pool.map(lambda _:self.f.reserve(),range(4)))
        self.assertEqual(len(set(eids)),1)
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda _:self.f.gateway.create(eids[0]),range(4)))
        self.assertEqual(len(self.f.provider.orders),1)
        self.f.provider.approve()
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda _:self.f.gateway.capture(eids[0]),range(4)))
        self.assertEqual(self.f.provider.capture_count(),1)

    def test_t11_process_crash_restart_wal(self):
        """A04-T11 abrupt separate-process exit preserves committed reserve."""
        script = ("import os,sys; from tests.helpers import Fixture; "
                  "f=Fixture(sys.argv[1]); f.reserve(); os._exit(29)")
        crashed=Path(self.tmp.name)/"crashed"
        crashed.mkdir()
        result=subprocess.run([sys.executable,"-c",script,str(crashed)],capture_output=True)
        self.assertEqual(result.returncode,29)
        with closing(sqlite3.connect(crashed/"ledger.db")) as db:
            row=db.execute("SELECT amount,held,state,create_request_id FROM effects").fetchone()
        self.assertEqual(row[:3],(100,1,"RESERVED"))
        self.assertEqual(len(row[3]),36)

    def test_t12_altered_readback(self):
        """A04-T12 mismatch is discrepancy and no completed payment."""
        eid=self.f.create(); self.f.provider.approve()
        def tamper(path,body):
            if "/checkout/" in path: body["purchase_units"][0]["payee"]["merchant_id"]="EVIL"
        self.f.provider.tamper=tamper
        self.assertEqual(self.f.gateway.capture(eid),"RECONCILIATION_PENDING")
        self.assertEqual(self.f.provider.capture_count(),0)

    def test_t12_missing_readback(self):
        """A04-T12 missing provider truth cannot close payment."""
        eid=self.f.create(); self.f.provider.approve(); self.f.gateway.capture(eid)
        self.f.provider.error=(500,"HTTP_UNCERTAIN")
        self.assertEqual(self.f.gateway.reconcile(eid),"RECONCILIATION_PENDING")

    def test_t13_exact_boundaries(self):
        """A04-T13 exact 1 / 49999 accepted in isolation, 50001 rejected."""
        self.assertEqual([cents(v) for v in (1,49999,50000)],["0.01","499.99","500.00"])
        for v in (0,50001,1.0,True,-1):
            with self.assertRaises(Denied): cents(v)
        for amount in (1,49999,50000):
            with tempfile.TemporaryDirectory() as root:
                f=Fixture(root,(amount,)); eid=f.reserve()
                self.assertEqual(f.gateway.status(eid)["amount"],amount)

    def test_t14_errors(self):
        """A04-T14 401/403/500 fail closed, classified, reservation retained."""
        for code in (401,403,500):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as root:
                f=Fixture(root); f.provider.error=(code,"HTTP_REJECTED" if code<500 else "HTTP_UNCERTAIN")
                eid=f.create()
                self.assertEqual(f.gateway.status(eid)["state"],"RECONCILIATION_PENDING")
                self.assertEqual(f.gateway.status(eid)["held"],1)

    def test_acceptance_missing_bad_signature_or_hash(self):
        for mutate in (lambda a:a["tests"].pop("A03-T01"),lambda a:a.update(status="NOT_RUN"),lambda a:a.update(tdd_sha256="wrong")):
            with self.subTest(mutate=mutate):
                a=copy.deepcopy(self.f.acceptance); mutate(a)
                self.f.admission.acceptance=self.f.sign("acceptance","commerce.a03.acceptance.v1",a)
                with self.assertRaises(Denied): self.f.reserve()
        self.f.admission.acceptance=self.f.acceptance_envelope
        (self.f.root/"LEAN_BUILD.txt").write_text("tampered")
        with self.assertRaises(Denied): self.f.reserve()
        self.assertEqual(self.f.provider.calls,[])

    def test_empty_original_obligations(self):
        self.f.mission["obligations"]=[]
        self.f.control["mission_hash"]=digest("commerce.mission.v1",self.f.mission)
        self.f.control["sequence"]+=1; self.f.apply()
        with self.assertRaises(Denied): self.f.reserve()

    def test_stale_quote_epoch_expiry(self):
        for key,value in (("quote_expires_at",999),("epoch",0),("expires_at",999)):
            original=self.f.permits[0][key]; self.f.permits[0][key]=value
            with self.assertRaises(Denied): self.f.reserve()
            self.f.permits[0][key]=original

    def test_control_replay(self):
        with self.assertRaises(Denied): self.f.apply()

    def test_provider_amount_change_pre_capture(self):
        eid=self.f.create(); self.f.provider.approve()
        self.f.provider.orders["ORDER1"]["purchase_units"][0]["amount"]["value"]="5.00"
        self.assertEqual(self.f.gateway.capture(eid),"RECONCILIATION_PENDING")
        self.assertEqual(self.f.provider.capture_count(),0)

    def test_recover_unknown_create_by_independent_get(self):
        self.f.provider.lost_create=True; eid=self.f.create()
        self.assertEqual(self.f.gateway.attach_order(eid,"ORDER1"),"APPROVAL_PENDING")
        self.f.provider.approve(); self.f.gateway.capture(eid)
        self.assertEqual(self.f.provider.capture_count(),1)

    def test_unknown_capture_approved_readback_does_not_retry(self):
        eid=self.f.create(); self.f.provider.approve()
        self.f.provider.error=(500,"HTTP_UNCERTAIN")
        self.f.gateway.capture(eid)  # Readback fails before dispatch.
        self.f.provider.error=None
        with self.f.gateway.transaction() as db:
            db.execute("UPDATE effects SET capture_started=1,state='CAPTURE_IN_FLIGHT' WHERE id=?",(eid,))
        self.assertEqual(self.f.gateway.reconcile(eid),"RECONCILIATION_PENDING")
        self.f.gateway.capture(eid)
        self.assertEqual(self.f.provider.capture_count(),0)

    def test_fake_stored_receipt_not_ground_truth(self):
        eid=self.f.create()
        with self.f.gateway.transaction() as db:
            db.execute("UPDATE effects SET receipt=?,state='CAPTURE_CONFIRMED',capture_started=1 WHERE id=?",('{"paid":true}',eid))
        with self.assertRaises(Denied): independent_readback(self.f.gateway.path,eid,self.f.admission,self.f.provider)

    def test_tampered_capture_readback(self):
        eid=self.f.create(); self.f.provider.approve(); self.f.gateway.capture(eid)
        self.f.provider.details["CAPTURE1"]["amount"]["value"]="0.01"
        self.assertEqual(self.f.gateway.reconcile(eid),"RECONCILIATION_PENDING")
        with self.assertRaises(Denied): independent_readback(self.f.gateway.path,eid,self.f.admission,self.f.provider)

    def test_refund_does_not_restore_spend(self):
        eid=self.f.create(); self.f.provider.approve(); self.f.gateway.capture(eid)
        self.f.provider.details["CAPTURE1"]["status"]="REFUNDED"
        self.f.provider.orders["ORDER1"]["purchase_units"][0]["payments"]["captures"][0]["status"]="REFUNDED"
        self.f.gateway.reconcile(eid)
        self.assertEqual(self.f.gateway.status(eid)["held"],1)

    def test_expired_after_reserve_no_calls(self):
        eid=self.f.create(); self.f.provider.approve(); self.f.now=1401
        n=len(self.f.provider.calls)
        with self.assertRaises(Denied): self.f.gateway.capture(eid)
        self.assertEqual(len(self.f.provider.calls),n)

    def test_t10_same_permit_for_different_effect_refused(self):
        with tempfile.TemporaryDirectory() as root:
            f=Fixture(root,(100,200))
            f.permits[1]["permit_id"]=f.permits[0]["permit_id"]
            def reserve(i):
                try: return f.reserve(i)
                except Denied: return None
            with concurrent.futures.ThreadPoolExecutor(2) as pool:
                values=list(pool.map(reserve,[0,1]))
            self.assertEqual(sum(v is not None for v in values),1)
            self.assertEqual(f.provider.calls,[])

    def test_t03_separate_process_reservations(self):
        """A04-T03 competing OS processes, both bound to the same signed mission."""
        (self.f.root/"public-trust.json").write_text(json.dumps(self.f.trust))
        (self.f.root/"acceptance-envelope.json").write_text(json.dumps(self.f.acceptance_envelope))
        for n in (0,1):
            m,p=self.f.envelopes(n)
            (self.f.root/f"mission-{n}.json").write_text(json.dumps(m))
            (self.f.root/f"permit-{n}.json").write_text(json.dumps(p))
        script=("import sys,json; from pathlib import Path; from commerce_adapter.contracts import Admission,Denied; "
                "from commerce_adapter.gateway import Gateway; from tests.helpers import FakePayPal; "
                "r=Path(sys.argv[1]); n=sys.argv[2]; read=lambda x:json.loads((r/x).read_text()); "
                "a=Admission(read('public-trust.json'),read('acceptance-envelope.json'),r,'TEST_ONLY',{'fixture':'MOCK_ONLY'},lambda:1000); "
                "g=Gateway(r/'ledger.db',a,FakePayPal()); "
                "\ntry: print(g.reserve(read('mission-'+n+'.json'),read('permit-'+n+'.json')))"
                "\nexcept Denied: sys.exit(3)")
        def run(n): return subprocess.run([sys.executable,"-c",script,str(self.f.root),str(n)],capture_output=True).returncode
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            codes=list(pool.map(run,[0,1]))
        self.assertEqual(sorted(codes),[0,3])

    def test_duplicate_provider_capture_rejected(self):
        eid=self.f.create(); self.f.provider.approve(); self.f.gateway.capture(eid)
        captures=self.f.provider.orders["ORDER1"]["purchase_units"][0]["payments"]["captures"]
        captures.append(copy.deepcopy(captures[0]))
        self.assertEqual(self.f.gateway.reconcile(eid),"RECONCILIATION_PENDING")
        with self.assertRaises(Denied): independent_readback(self.f.gateway.path,eid,self.f.admission,self.f.provider)
        n=len(self.f.provider.calls)
        with self.assertRaisesRegex(Denied,"SAFETY_STOP"): self.f.gateway.capture(eid)
        self.assertEqual(len(self.f.provider.calls),n)

    def test_wrong_capture_order_rejected(self):
        eid=self.f.create(); self.f.provider.approve(); self.f.gateway.capture(eid)
        self.f.provider.details["CAPTURE1"]["supplementary_data"]["related_ids"]["order_id"]="OTHER"
        self.assertEqual(self.f.gateway.reconcile(eid),"RECONCILIATION_PENDING")
        with self.assertRaises(Denied): independent_readback(self.f.gateway.path,eid,self.f.admission,self.f.provider)
