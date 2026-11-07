import json
import secrets
import sqlite3
import time
import uuid
from functools import wraps
from contextlib import contextmanager
from urllib.parse import urlparse
from .contracts import Denied, canonical, cents, digest, parse_cents, provider_id, require


class ProviderError(Exception):
    def __init__(self, status=0, code="NETWORK_UNCERTAIN", provider_name=None, issues=(), debug_id=None):
        self.status = status
        self.code = code
        self.provider_name, self.issues, self.debug_id = provider_name, issues, debug_id
        super().__init__(code)


SCHEMA = """
CREATE TABLE IF NOT EXISTS missions(id TEXT PRIMARY KEY, hash TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS controls(mission_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL, envelope TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS effects(
 id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, obligation_id TEXT NOT NULL,
 version TEXT NOT NULL, slot TEXT NOT NULL, permit_id TEXT NOT NULL UNIQUE,
 mission TEXT NOT NULL, permit TEXT NOT NULL, intent TEXT NOT NULL,
 amount INTEGER NOT NULL CHECK(amount>0), held INTEGER NOT NULL CHECK(held IN (0,1)),
 state TEXT NOT NULL, create_request_id TEXT NOT NULL UNIQUE, capture_request_id TEXT NOT NULL UNIQUE,
 order_id TEXT UNIQUE, capture_id TEXT UNIQUE, approval_url TEXT, callback_state TEXT NOT NULL,
 capture_started INTEGER NOT NULL DEFAULT 0, receipt TEXT,
 UNIQUE(mission_id, obligation_id, version, slot));
CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, at REAL NOT NULL, effect_id TEXT, kind TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS flags(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def logged_denial(function):
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        try:
            return function(self, *args, **kwargs)
        except Denied as error:
            # The failed operation rolled back; preserve its denial separately.
            with self.transaction() as db:
                self.event(db, None, "DENIED", {"operation": function.__name__, "code": str(error)})
            raise
    return wrapped


class Gateway:
    """Single-host ledger. BEGIN IMMEDIATE serializes reservations, commands and revocations.

    Network requests run under the write lock: the dispatch is the revocation
    linearization point. A revocation committed before dispatch prevents dispatch;
    already dispatched provider requests cannot be undone by local revocation.
    """
    def __init__(self, path, admission, provider):
        self.path, self.admission, self.provider = path, admission, provider
        with self.connect() as db:
            db.executescript(SCHEMA)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=60, isolation_level=None, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def event(self, db, effect, kind, data):
        db.execute("INSERT INTO events(at,effect_id,kind,data) VALUES(?,?,?,?)",
                   (time.time(), effect, kind, canonical(data).decode()))

    @logged_denial
    def apply_control(self, mission_id, envelope):
        p = self.admission.control(envelope)
        require(p["mission_id"] == mission_id, "CONTROL_MISSION")
        with self.transaction() as db:
            old = db.execute("SELECT sequence,envelope FROM controls WHERE mission_id=?", (mission_id,)).fetchone()
            if old:
                require(p["sequence"] > old["sequence"], "CONTROL_REPLAY")
                oldp = self.admission.control(json.loads(old["envelope"]))
                require(p["epoch"] >= oldp["epoch"], "EPOCH_ROLLBACK")
                require(set(oldp["revoked_permits"]) <= set(p["revoked_permits"]), "REVOCATION_ROLLBACK")
            db.execute("INSERT OR REPLACE INTO controls VALUES(?,?,?)", (mission_id, p["sequence"], canonical(envelope).decode()))
            self.event(db, None, "CONTROL_APPLIED", {"mission_id": mission_id, "sequence": p["sequence"]})

    def check(self, db, mission, permit):
        # FORMAL-001 (B-COM-1/2): forget any earlier decision BEFORE the admission runs; only the attestation produced
        # by THIS check, inside THIS transaction, can authorize the next external POST.
        self._lean = None
        if hasattr(self.admission, "last_decision"):
            self.admission.last_decision = None
        require(db.execute("SELECT 1 FROM flags WHERE key='SAFETY_STOP'").fetchone() is None, "SAFETY_STOP")
        mid = mission["payload"]["mission_id"]
        row = db.execute("SELECT envelope FROM controls WHERE mission_id=?", (mid,)).fetchone()
        require(row is not None, "CURRENT_CONTROL_MISSING")
        control = self.admission.control(json.loads(row[0]))
        result = self.admission.check(mission, permit, control)
        self._lean = (id(db), getattr(self.admission, "last_decision", None))
        return result

    def require_lean(self, db, eid):
        """FORMAL-001 mandatory Lean admission at the external-effect boundary. Every POST to a provider that is not a
        MOCK_ONLY test double needs a qualified Lean verdict T computed by the admission check that ran immediately
        before it in the SAME transaction, for EXACTLY this effect's intent. Admissions that do not compute
        `admission .full` (A04 base Admission: signed claim only; v1 bridge: ZTL witness only) never produce one."""
        if getattr(self.provider, "evidence_mode", None) == "MOCK_ONLY":
            return
        tx, att = getattr(self, "_lean", None) or (None, None)
        intent = json.loads(self.row(db, eid)["intent"])
        require(tx == id(db) and isinstance(att, dict) and att.get("lean_verdict") == "T"
                and att.get("action_digest") == digest("commerce.intent.v1", intent), "LEAN_ADMISSION_REQUIRED")
        # FORMAL-003: the attestation relied on is journaled in the same transaction as the dispatch it authorizes.
        self.event(db, eid, "LEAN_ATTESTED", {"lean_verdict": "T", "action_digest": att["action_digest"],
                                              "source": att.get("source"),
                                              "evaluated_at": int(att["evaluated_at"]) if isinstance(att.get("evaluated_at"), (int, float)) else None})

    @logged_denial
    def reserve(self, mission, permit):
        with self.transaction() as db:
            m, p, intent = self.check(db, mission, permit)
            mh = digest("commerce.mission.v1", m)
            existing = db.execute("SELECT hash FROM missions WHERE id=?", (m["mission_id"],)).fetchone()
            require(existing is None or existing[0] == mh, "MISSION_IMMUTABLE")
            key = [m["mission_id"], intent["obligation_id"], m["contract_version"], intent["effect_slot"]]
            eid = digest("commerce.effect.v1", key)
            old = db.execute("SELECT * FROM effects WHERE id=?", (eid,)).fetchone()
            if old:
                require(old["permit_id"] == p["permit_id"] and json.loads(old["intent"]) == intent
                        and old["mission"] == canonical(mission).decode()
                        and old["permit"] == canonical(permit).decode(), "EFFECT_IDENTITY_CONFLICT")
                self.event(db, eid, "IDEMPOTENT_LOOKUP", {"new_permit_use": False, "new_reservation": False})
                return eid
            require(db.execute("SELECT 1 FROM effects WHERE permit_id=?", (p["permit_id"],)).fetchone() is None, "PERMIT_CONSUMED")
            total = db.execute("SELECT COALESCE(SUM(amount),0) FROM effects WHERE held=1").fetchone()[0]
            local = db.execute("SELECT COALESCE(SUM(amount),0) FROM effects WHERE held=1 AND mission_id=?", (m["mission_id"],)).fetchone()[0]
            require(total + intent["amount_cents"] <= 50000 and local + intent["amount_cents"] <= m["cap_cents"], "BUDGET_EXCEEDED")
            db.execute("INSERT OR IGNORE INTO missions VALUES(?,?,?)", (m["mission_id"], mh, canonical(mission).decode()))
            db.execute("""INSERT INTO effects(id,mission_id,obligation_id,version,slot,permit_id,mission,permit,intent,
                amount,held,state,create_request_id,capture_request_id,callback_state)
                VALUES(?,?,?,?,?,?,?,?,?,?,1,'RESERVED',?,?,?)""",
                (eid, m["mission_id"], intent["obligation_id"], m["contract_version"], intent["effect_slot"], p["permit_id"],
                 canonical(mission).decode(), canonical(permit).decode(), canonical(intent).decode(), intent["amount_cents"],
                 str(uuid.uuid4()), str(uuid.uuid4()), secrets.token_urlsafe(32)))
            self.event(db, eid, "RESERVED", {"amount_cents": intent["amount_cents"], "budget_used_cents": total + intent["amount_cents"]})
            return eid

    def row(self, db, eid):
        r = db.execute("SELECT * FROM effects WHERE id=?", (eid,)).fetchone()
        require(r is not None, "UNKNOWN_EFFECT")
        return r

    def recheck(self, db, row):
        return self.check(db, json.loads(row["mission"]), json.loads(row["permit"]))

    def call(self, db, eid, method, path, body=None, request_id=None):
        started = time.monotonic()
        # Persist a pre-dispatch marker separately in create/capture before here.
        if method != "GET":
            self.require_lean(db, eid)
            self._lean = None  # single use
        try:
            result = self.provider.request(method, path, body, request_id)
            self.event(db, eid, "NETWORK", {"method": method, "operation": path.split("/")[-1],
                       "http_status": result[0], "duration_ms": int((time.monotonic()-started)*1000)})
            return result[1]
        except ProviderError as e:
            self.event(db, eid, "NETWORK_ERROR", {"method": method, "http_status": e.status,
                       "code": e.code, "provider_name": e.provider_name, "issues": list(e.issues), "debug_id": e.debug_id,
                       "duration_ms": int((time.monotonic()-started)*1000)})
            raise

    def discrepancy(self, db, eid, error):
        code = str(error) if isinstance(error, (Denied, ProviderError)) else "MALFORMED_PROVIDER_DATA"
        self.event(db, eid, "READBACK_DISCREPANCY", {"code": code})
        if isinstance(error, Denied) and code not in ("UNKNOWN_ORDER_REQUIRES_MERCHANT_LOOKUP",):
            db.execute("INSERT OR REPLACE INTO flags VALUES('SAFETY_STOP',?)", (code,))
            self.event(db, eid, "SAFETY_STOP", {"code": code, "external_writes_disabled": True})

    @logged_denial
    def create(self, eid, port=8765):
        require(type(port) is int and 1024 <= port <= 65535, "CALLBACK_PORT")
        # Commit uncertainty BEFORE dispatch. A process killed after this commit
        # must reconcile, even if it died before the request actually left.
        with self.transaction() as db:
            r = self.row(db, eid)
            self.recheck(db, r)
            if r["state"] != "RESERVED":
                return r["state"]
            self.require_lean(db, eid)  # FORMAL-001: refuse before any state change when no Lean T exists
            db.execute("UPDATE effects SET state='CREATE_IN_FLIGHT' WHERE id=?", (eid,))
            self.event(db, eid, "CREATE_INTENT", {"request_id": r["create_request_id"]})
        with self.transaction() as db:
            r = self.row(db, eid)
            # No second process can dispatch: only this invocation transitioned
            # RESERVED. A crash leaves CREATE_IN_FLIGHT, never auto-retried.
            try:
                self.recheck(db, r)
                i = json.loads(r["intent"])
                callback = f"http://127.0.0.1:{port}/return?state={r['callback_state']}"
                body = {"intent": "CAPTURE", "purchase_units": [{"reference_id": eid, "custom_id": eid,
                        "invoice_id": eid, "description": "Sandbox bundle; synthetic service fulfilment only",
                        "amount": {"currency_code": "USD", "value": cents(i["amount_cents"])},
                        "payee": {"merchant_id": i["merchant_id"]}}],
                        "payment_source": {"paypal": {"experience_context": {"user_action": "PAY_NOW",
                        "shipping_preference": "NO_SHIPPING", "return_url": callback,
                        "cancel_url": f"http://127.0.0.1:{port}/cancel"}}}}
                order = self.call(db, eid, "POST", "/v2/checkout/orders", body, r["create_request_id"])
                oid = provider_id(order["id"])
                db.execute("UPDATE effects SET order_id=?,state='APPROVAL_PENDING' WHERE id=?", (oid, eid))
                links = [x["href"] for x in order.get("links", []) if x.get("rel") in ("approve", "payer-action")]
                if links:
                    u = urlparse(links[0])
                    require(u.scheme == "https" and u.hostname == "www.sandbox.paypal.com"
                            and u.username is None and u.password is None and u.port in (None,443), "UNSAFE_APPROVAL_URL")
                    db.execute("UPDATE effects SET approval_url=? WHERE id=?", (links[0], eid))
                self.event(db, eid, "ORDER_CREATED", {"order_id": oid, "provider_status": order.get("status", "UNKNOWN")})
            except (ProviderError, Denied, KeyError, TypeError, ValueError) as error:
                db.execute("UPDATE effects SET state='RECONCILIATION_PENDING' WHERE id=?", (eid,))
                self.event(db, eid, "CREATE_UNCERTAIN", {"reservation_retained": True})
                if not isinstance(error, ProviderError):
                    self.discrepancy(db, eid, error)
            return self.row(db, eid)["state"]

    def validate_order(self, r, order):
        i = json.loads(r["intent"])
        require(order["id"] == r["order_id"] and order["intent"] == "CAPTURE", "ORDER_MISMATCH")
        units = order["purchase_units"]
        require(len(units) == 1, "PURCHASE_UNITS_MISMATCH")
        unit = units[0]
        require(unit["reference_id"] == unit["custom_id"] == unit["invoice_id"] == r["id"], "EFFECT_JOIN_MISMATCH")
        require(unit["payee"]["merchant_id"] == i["merchant_id"], "PAYEE_MISMATCH")
        require(unit["amount"]["currency_code"] == "USD"
                and parse_cents(unit["amount"]["value"]) == r["amount"], "AMOUNT_MISMATCH")
        return unit

    def reconcile_in(self, db, eid):
        r = self.row(db, eid)
        require(r["order_id"], "UNKNOWN_ORDER_REQUIRES_MERCHANT_LOOKUP")
        order = self.call(db, eid, "GET", f"/v2/checkout/orders/{provider_id(r['order_id'])}")
        unit = self.validate_order(r, order)
        self.event(db, eid, "ORDER_READBACK", {"order_id": r["order_id"], "provider_status": order["status"]})
        captures = unit.get("payments", {}).get("captures", [])
        require(len(captures) <= 1, "MULTIPLE_CAPTURES")
        if captures:
            c = captures[0]
            cid = provider_id(c["id"])
            detail = self.call(db, eid, "GET", f"/v2/payments/captures/{cid}")
            require(detail["id"] == cid and detail["status"] == c["status"], "CAPTURE_READBACK_MISMATCH")
            for item in (c, detail):
                require(item["amount"]["currency_code"] == "USD"
                        and parse_cents(item["amount"]["value"]) == r["amount"], "CAPTURE_AMOUNT_MISMATCH")
            require(detail["supplementary_data"]["related_ids"]["order_id"] == r["order_id"], "CAPTURE_ORDER_MISMATCH")
            if c["status"] == "COMPLETED":
                require(order["status"] == "COMPLETED" and r["capture_started"] == 1, "UNAUTHORIZED_OR_NONFINAL_CAPTURE")
                receipt = {"schema": "commerce.payment_readback.v1", "effect_id": eid,
                           "order_id": r["order_id"], "capture_id": cid, "currency": "USD",
                           "amount_cents": r["amount"], "merchant_id": json.loads(r["intent"])["merchant_id"],
                           "status": "COMPLETED", "source": self.provider.evidence_mode,
                           "readback_at": int(time.time()), "mission_complete": False,
                           "order_digest": digest("paypal.order.readback.v1", order),
                           "capture_digest": digest("paypal.capture.readback.v1", detail)}
                db.execute("UPDATE effects SET capture_id=?,state='CAPTURE_CONFIRMED',receipt=? WHERE id=?", (cid, canonical(receipt).decode(), eid))
                self.event(db, eid, "CAPTURE_READBACK", receipt)
                return "CAPTURE_CONFIRMED"
            if c["status"] == "DECLINED":
                # Conservative policy: terminal decline ends this effect but
                # does not delegate fresh spending authority. No automatic
                # refund/decline recycling of the original mission budget.
                db.execute("UPDATE effects SET state='FAILED_HELD' WHERE id=?", (eid,))
                self.event(db, eid, "CAPTURE_DECLINED_READBACK", {"capture_id": cid})
                return "FAILED_HELD"
            db.execute("UPDATE effects SET state='RECONCILIATION_PENDING' WHERE id=?", (eid,))
            return "RECONCILIATION_PENDING"
        require(r["state"] != "CAPTURE_CONFIRMED", "CONFIRMED_CAPTURE_DISAPPEARED")
        if r["capture_started"]:
            # APPROVED after lost reply is not evidence that a capture did NOT occur.
            db.execute("UPDATE effects SET state='RECONCILIATION_PENDING' WHERE id=?", (eid,))
            return "RECONCILIATION_PENDING"
        approved = order["status"] == "APPROVED" and bool(order.get("payer", {}).get("payer_id")
                    or order.get("payment_source", {}).get("paypal", {}).get("account_id"))
        state = "APPROVED" if approved else "APPROVAL_PENDING"
        db.execute("UPDATE effects SET state=? WHERE id=?", (state, eid))
        return state

    def reconcile(self, eid):
        with self.transaction() as db:
            try:
                return self.reconcile_in(db, eid)
            except (ProviderError, Denied, KeyError, TypeError, ValueError) as e:
                self.discrepancy(db, eid, e)
                db.execute("UPDATE effects SET state='RECONCILIATION_PENDING' WHERE id=?", (eid,))
                return "RECONCILIATION_PENDING"

    def attach_order(self, eid, order_id):
        # Lost create reply: operator locates candidate in merchant dashboard.
        # Candidate is never trusted until direct GET joins all immutable fields.
        with self.transaction() as db:
            r = self.row(db, eid)
            require(r["order_id"] is None and r["state"] in ("CREATE_IN_FLIGHT", "RECONCILIATION_PENDING"), "ATTACH_NOT_ALLOWED")
            candidate = dict(r)
            candidate["order_id"] = provider_id(order_id)
            order = self.call(db, eid, "GET", f"/v2/checkout/orders/{order_id}")
            self.validate_order(candidate, order)
            db.execute("UPDATE effects SET order_id=? WHERE id=?", (order_id, eid))
            self.event(db, eid, "ORDER_RECOVERED", {"order_id": order_id})
        return self.reconcile(eid)

    @logged_denial
    def capture(self, eid):
        with self.transaction() as db:
            r = self.row(db, eid)
            self.recheck(db, r)  # Denied/revoked => zero provider calls, including GET.
            if r["capture_started"] or r["state"] == "FAILED_RELEASED":
                return r["state"]
            try:
                state = self.reconcile_in(db, eid)
            except (ProviderError, Denied, KeyError, TypeError, ValueError) as error:
                db.execute("UPDATE effects SET state='RECONCILIATION_PENDING' WHERE id=?", (eid,))
                self.discrepancy(db, eid, error)
                return "RECONCILIATION_PENDING"
            if state != "APPROVED":
                return state
            self.recheck(db, r)
            self.require_lean(db, eid)  # FORMAL-001: refuse before capture_started when no Lean T exists
            db.execute("UPDATE effects SET state='CAPTURE_IN_FLIGHT',capture_started=1 WHERE id=?", (eid,))
            self.event(db, eid, "CAPTURE_INTENT", {"request_id": r["capture_request_id"]})
        with self.transaction() as db:
            r = self.row(db, eid)
            try:
                self.recheck(db, r)
                self.call(db, eid, "POST", f"/v2/checkout/orders/{provider_id(r['order_id'])}/capture", {}, r["capture_request_id"])
                # A POST body is NEVER completion evidence. Separate authenticated GETs.
                return self.reconcile_in(db, eid)
            except (ProviderError, Denied, KeyError, TypeError, ValueError) as error:
                db.execute("UPDATE effects SET state='RECONCILIATION_PENDING' WHERE id=?", (eid,))
                self.event(db, eid, "CAPTURE_UNCERTAIN", {"reservation_retained": True})
                if not isinstance(error, ProviderError):
                    self.discrepancy(db, eid, error)
                return "RECONCILIATION_PENDING"

    def status(self, eid):
        with self.connect() as db:
            r = self.row(db, eid)
            return {k: r[k] for k in ("id", "state", "amount", "held", "order_id", "capture_id", "approval_url")}
