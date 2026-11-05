import copy
import hashlib
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from commerce_adapter.contracts import Denied,digest
from commerce_bridge.installation import apply_external_control,checked_trust,gateway_for,install_package,validate_package
from bridge_tests.fixtures import Fixture
from tests.helpers import FakePayPal


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.f=Fixture(self.tmp.name)
        f=self.f
        f.now=int(time.time())
        f.mission["deadline"]=f.now+600
        f.mh=digest("commerce.mission.v1",f.mission)
        f.control.update(mission_hash=f.mh,expires_at=f.now+600)
        f.policy.update(mission_hash=f.mh,expires_at=f.now+600)
        f.observation.update(mission_hash=f.mh,observed_at=f.now)
        f.set_reports([True,True,True],observed_at=f.now)
        gateway,_,mission,permit=f.gateway()
        self.package=f.root/"package"
        self.package.mkdir()
        shutil.copytree(f.root/"accepted",self.package/"a03-evidence")
        shutil.copytree(f.root/"bundles",self.package/"bridge-evidence")
        payloads={"trust.json":f.trust,"a03-acceptance.json":gateway.admission.acceptance,
                  "current-mission.json":mission,"current-permit.json":permit,
                  "current-control.json":f.sign("control","commerce.control.v1",f.control),
                  "bridge-policy.json":f.bundle()["policy"]}
        for name,value in payloads.items(): (self.package/name).write_text(json.dumps(value))
        self.trust_file=f.root/"out-of-band-public-trust.json"
        self.trust_file.write_text(json.dumps(f.trust))
        self.trust_hash=hashlib.sha256(self.trust_file.read_bytes()).hexdigest()
        self.sign_manifest()

    def sign_manifest(self):
        files={p.relative_to(self.package).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
               for p in self.package.rglob("*") if p.is_file() and p.name!="installation-manifest.json"}
        manifest={"schema":"commerce.bridge.installation.v1","status":"ACCEPTED","mission_hash":self.f.mh,
                  "files":files,"fixture":"CONFORMANCE_ONLY_NO_NATIVE_AUTHORITY"}
        (self.package/"installation-manifest.json").write_text(json.dumps(self.f.sign("acceptance","commerce.bridge.installation.v1",manifest)))

    def tearDown(self): self.tmp.cleanup()

    def test_verified_install_and_repeated_preflight_with_ledger(self):
        target=self.f.root/"installed-test-only"
        install_package(self.package,target,self.f.sources,self.trust_file,self.trust_hash)
        provider=FakePayPal()
        first,mission,permit=gateway_for(target,self.f.sources,self.trust_file,self.trust_hash,provider)
        eid=first.reserve(mission,permit)
        second,_,_=gateway_for(target,self.f.sources,self.trust_file,self.trust_hash,provider)
        self.assertEqual(second.status(eid)["state"],"RESERVED")
        self.assertEqual(provider.calls,[])

    def test_external_manifest_and_trust_substitution_rejected(self):
        with self.assertRaisesRegex(Denied,"OPERATOR_TRUST_FINGERPRINT"):
            checked_trust(self.trust_file,"0"*64)
        path=self.package/"installation-manifest.json"
        manifest=json.loads(path.read_text()); manifest["signature"]="Zm9yZ2Vk"; path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(Denied,"INVALID_SIGNATURE"):
            validate_package(self.package,self.f.sources,self.trust_file,self.trust_hash)

    def test_private_key_file_not_installable(self):
        (self.package/"operator-private.key").write_text("CONFORMANCE_ONLY_FAKE_KEY")
        self.sign_manifest()
        with self.assertRaisesRegex(Denied,"UNEXPECTED_PACKAGE_FILE"):
            validate_package(self.package,self.f.sources,self.trust_file,self.trust_hash)

    def test_upstream_receipt_tamper_rejected(self):
        (self.package/"a03-evidence/A01_RESULT.json").write_text("tampered")
        self.sign_manifest()
        with self.assertRaisesRegex(Denied,"A03_EVIDENCE_HASH"):
            validate_package(self.package,self.f.sources,self.trust_file,self.trust_hash)

    def test_existing_installation_never_overwritten(self):
        target=self.f.root/"existing"
        target.mkdir(); (target/"operator-file").write_text("keep")
        with self.assertRaisesRegex(Denied,"INSTALLATION_ALREADY_EXISTS"):
            install_package(self.package,target,self.f.sources,self.trust_file,self.trust_hash)
        self.assertEqual((target/"operator-file").read_text(),"keep")

    def test_revocation_independent_of_stale_acceptance_or_lean(self):
        provider=FakePayPal()
        gateway,mission,permit=gateway_for(self.package,self.f.sources,self.trust_file,self.trust_hash,provider)
        eid=gateway.reserve(mission,permit)
        self.f.control["sequence"]=2
        self.f.control["revoked_permits"]=["CONFORMANCE-PERMIT"]
        # Breaking the acceptance file must not block an authenticated revocation.
        (self.package/"a03-acceptance.json").write_text("invalid")
        receipt=apply_external_control(self.package,self.trust_file,self.trust_hash,
            self.f.sign("control","commerce.control.v1",self.f.control))
        self.assertEqual(receipt["sequence"],2)
        with self.assertRaisesRegex(Denied,"REVOKED_PERMIT"): gateway.create(eid)
        self.assertEqual(provider.calls,[])
