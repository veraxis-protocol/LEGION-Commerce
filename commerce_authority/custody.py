"""WO-COM-AUTHORITY-005: custody of Ed25519 role keys on Windows.

Two independent protection layers on every private key:
  1. A custodian passphrase (scrypt n=2^15, r=8, p=1 -> ChaCha20-Poly1305; AAD binds role and key id).
     The passphrase is read only from an interactive console (getpass); it is never accepted from
     arguments, environment, files or piped input, so an automated agent cannot sign as a role.
  2. Windows DPAPI (CurrentUser scope, role-bound entropy) over the whole sealed record, and an ACL
     on the role directory restricted to the custodian account, SYSTEM and Administrators.

DPAPI binds the file to ONE Windows account. Several roles stored under one account are NOT
independent custody; independence comes from distinct custodians holding distinct passphrases,
and for the acceptance reviewer and admission issuer, from their own accounts/hosts.
"""
import base64
import ctypes
import getpass
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from commerce_adapter.contracts import Denied, canonical, require

from .roles import ROLES

KDF = {"name": "scrypt", "n": 2 ** 15, "r": 8, "p": 1, "length": 32}
MIN_PASSPHRASE = 12


def default_root():
    return Path(os.environ["LOCALAPPDATA"]) / "Veraxis" / "LEGION-Commerce" / "authority"


def forbidden_location(path):
    """Private key material may never be written under Dropbox, OneDrive, a git work tree or the repo."""
    p = Path(path).resolve()
    text = str(p).lower()
    if any(tag in text for tag in ("\\dropbox", "\\onedrive", "\\github", "\\documents\\chatgpt")):
        return True
    return any((q / ".git").exists() for q in [p, *p.parents])


# ---------------------------------------------------------------- DPAPI
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(data):
    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    return _Blob(len(data), buf), buf


def _dpapi(data, entropy, protect):
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                   wintypes.DWORD, ctypes.POINTER(_Blob)]
    fn.restype = wintypes.BOOL
    inp, keep1 = _blob(data); ent, keep2 = _blob(entropy); out = _Blob()
    # 0x1 = CRYPTPROTECT_UI_FORBIDDEN; CurrentUser scope (no LOCAL_MACHINE flag).
    ok = fn(ctypes.byref(inp), None, ctypes.byref(ent), None, None, 0x1, ctypes.byref(out))
    require(bool(ok), "DPAPI_PROTECT_FAILED" if protect else "DPAPI_UNPROTECT_FAILED")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel.LocalFree(out.pbData)


def _entropy(role):
    return b"LEGION-Commerce-authority-v1:" + role.encode("ascii")


# ---------------------------------------------------------------- identity / ACL
def windows_identity():
    shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    out = subprocess.run([str(shell), "-NoProfile", "-NonInteractive", "-Command",
                          "$i=[Security.Principal.WindowsIdentity]::GetCurrent(); \"$($i.User.Value) $($i.Name)\""],
                         capture_output=True, text=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    sid, _, name = out.stdout.strip().partition(" ")
    require(out.returncode == 0 and re.fullmatch(r"S-1-[0-9-]+", sid), "WINDOWS_IDENTITY")
    return {"sid": sid, "account": name, "host": os.environ.get("COMPUTERNAME", "")}


def restrict_acl(path, sid):
    """Owner account + SYSTEM + local Administrators only; inheritance removed."""
    r = subprocess.run(["icacls.exe", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F",
                        "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"], capture_output=True, timeout=20,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    require(r.returncode == 0, "CUSTODY_ACL")


def acl_text(path):
    r = subprocess.run(["icacls.exe", str(path)], capture_output=True, text=True, timeout=20,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.stdout


# ---------------------------------------------------------------- passphrase (interactive only)
def interactive_console():
    """True only for a real console input handle. isatty() is NOT sufficient on Windows: the NUL
    device (stdin of automated tools) is a character device and reports isatty() == True."""
    if sys.stdin is None or not sys.stdin.isatty():
        return False
    if os.name != "nt":
        return True
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetStdHandle.restype = wintypes.HANDLE
    k.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    mode = wintypes.DWORD()
    return bool(k.GetConsoleMode(k.GetStdHandle(wintypes.DWORD(-10 & 0xFFFFFFFF)), ctypes.byref(mode)))


def interactive_passphrase(prompt, confirm=False, reader=None):
    """Console only. `reader` exists solely for the automated test harness (in-memory test roots)."""
    if reader is not None:
        value = reader(prompt)
    else:
        require(interactive_console(), "INTERACTIVE_CUSTODIAN_CONSOLE_REQUIRED")
        value = getpass.getpass(prompt)
        if confirm:
            require(getpass.getpass("Repeat passphrase: ") == value, "PASSPHRASE_CONFIRMATION_MISMATCH")
    require(isinstance(value, str) and len(value) >= MIN_PASSPHRASE, "PASSPHRASE_TOO_SHORT")
    return value


def _kek(passphrase, salt):
    return Scrypt(salt=salt, length=KDF["length"], n=KDF["n"], r=KDF["r"], p=KDF["p"]).derive(passphrase.encode("utf-8"))


def key_id(public_raw):
    return hashlib.sha256(public_raw).hexdigest()[:24]


def public_b64(private):
    return base64.b64encode(private.public_key().public_bytes_raw()).decode()


# ---------------------------------------------------------------- store
class Store:
    """Directory layout: <root>/<role>/<key_id>.key.dpapi  (private, ACL-restricted)
                         <root>/registry.json               (public metadata only)"""

    def __init__(self, root=None, dpapi=True):
        self.root = Path(root) if root is not None else default_root()
        self.dpapi = dpapi  # False only in the in-memory/test harness on non-Windows CI; never for operations

    def role_dir(self, role):
        require(role in ROLES, "UNKNOWN_ROLE")
        return self.root / role

    def registry_path(self):
        return self.root / "registry.json"

    def registry(self):
        p = self.registry_path()
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"schema": "commerce.authority.registry.v1", "keys": []}

    def save_registry(self, reg):
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.registry_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(reg, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, self.registry_path())

    def generate(self, role, custodian, passphrase, identity=None):
        require(role in ROLES, "UNKNOWN_ROLE")
        require(not forbidden_location(self.root), "PRIVATE_KEY_LOCATION_FORBIDDEN")
        identity = identity or windows_identity()
        private = Ed25519PrivateKey.generate()
        raw_pub = private.public_key().public_bytes_raw()
        kid = key_id(raw_pub)
        salt, nonce = os.urandom(16), os.urandom(12)
        aad = f"{role}|{kid}".encode()
        sealed = ChaCha20Poly1305(_kek(passphrase, salt)).encrypt(nonce, private.private_bytes_raw(), aad)
        record = {"schema": "commerce.authority.sealed-key.v1", "role": role, "key_id": kid,
                  "public_key": base64.b64encode(raw_pub).decode(), "kdf": KDF,
                  "salt": base64.b64encode(salt).decode(), "nonce": base64.b64encode(nonce).decode(),
                  "ciphertext": base64.b64encode(sealed).decode(), "created_at": int(time.time())}
        body = json.dumps(record, sort_keys=True).encode()
        blob = _dpapi(body, _entropy(role), True) if self.dpapi else b"NODPAPI" + body
        d = self.role_dir(role); d.mkdir(parents=True, exist_ok=True)
        if self.dpapi:
            # The ACL always names the REAL account that owns the DPAPI blob (not declared metadata).
            restrict_acl(self.root, windows_identity()["sid"])
        path = d / f"{kid}.key.dpapi"
        require(not path.exists(), "KEY_ID_COLLISION")
        with open(path, "xb") as f:
            f.write(blob)
        reg = self.registry()
        reg["keys"].append({"role": role, "key_id": kid, "public_key": record["public_key"], "status": "PENDING_ENROLLMENT",
                            "custodian": custodian, "custody": {"mechanism": "passphrase(scrypt)+ChaCha20-Poly1305 inside DPAPI CurrentUser" if self.dpapi else "TEST_NO_DPAPI",
                                                                  "windows_account_sid": identity["sid"], "windows_account": identity["account"],
                                                                  "host": identity["host"], "passphrase_gated": True,
                                                                  "acl": "custodian account, SYSTEM, Administrators" if self.dpapi else "TEST"},
                            "created_at": record["created_at"]})
        self.save_registry(reg)
        # Wipe what Python lets us drop; the private key object is released here.
        del private
        return kid

    def entry(self, kid):
        for e in self.registry()["keys"]:
            if e["key_id"] == kid:
                return e
        raise Denied("UNKNOWN_KEY_ID")

    def active(self, role):
        keys = [e for e in self.registry()["keys"] if e["role"] == role and e["status"] == "ACTIVE"]
        require(len(keys) == 1, "NO_SINGLE_ACTIVE_KEY_FOR_ROLE")
        return keys[0]

    def set_status(self, kid, status, **extra):
        reg = self.registry()
        hit = [e for e in reg["keys"] if e["key_id"] == kid]
        require(len(hit) == 1, "UNKNOWN_KEY_ID")
        hit[0]["status"] = status; hit[0].update(extra)
        self.save_registry(reg)

    def unseal(self, role, kid, passphrase):
        """Return the private key for ONE signing operation. Refuses revoked/retired keys."""
        e = self.entry(kid)
        require(e["role"] == role, "CROSS_ROLE_KEY_USE")
        require(e["status"] in ("ACTIVE", "PENDING_ENROLLMENT"), "KEY_NOT_ACTIVE")
        blob = (self.role_dir(role) / f"{kid}.key.dpapi").read_bytes()
        body = _dpapi(blob, _entropy(role), False) if self.dpapi else blob[len(b"NODPAPI"):]
        record = json.loads(body)
        require(record["role"] == role and record["key_id"] == kid, "SEALED_KEY_IDENTITY")
        try:
            raw = ChaCha20Poly1305(_kek(passphrase, base64.b64decode(record["salt"]))).decrypt(
                base64.b64decode(record["nonce"]), base64.b64decode(record["ciphertext"]), f"{role}|{kid}".encode())
        except Exception:
            raise Denied("WRONG_PASSPHRASE_OR_TAMPERED_KEY") from None
        private = Ed25519PrivateKey.from_private_bytes(raw)
        require(public_b64(private) == record["public_key"] == e["public_key"], "SEALED_KEY_PUBLIC_MISMATCH")
        return private

    # -------- custodian backup (passphrase-encrypted only, never DPAPI, never in cloud/repo)
    def export_backup(self, role, kid, passphrase, destination):
        require(not forbidden_location(destination), "BACKUP_LOCATION_FORBIDDEN")
        private = self.unseal(role, kid, passphrase)
        salt, nonce = os.urandom(16), os.urandom(12)
        sealed = ChaCha20Poly1305(_kek(passphrase, salt)).encrypt(nonce, private.private_bytes_raw(), f"backup|{role}|{kid}".encode())
        out = {"schema": "commerce.authority.backup.v1", "role": role, "key_id": kid, "public_key": public_b64(private), "kdf": KDF,
               "salt": base64.b64encode(salt).decode(), "nonce": base64.b64encode(nonce).decode(), "ciphertext": base64.b64encode(sealed).decode()}
        with open(destination, "x", encoding="utf-8") as f:
            json.dump(out, f)
        return {"backup_written": True, "key_id": kid}


def sign(private, domain, payload):
    """Exact A04 envelope format: {"payload", "signature": b64(Ed25519(domain||0||canonical(payload)))}."""
    msg = domain.encode("ascii") + b"\x00" + canonical(payload)
    return {"payload": payload, "signature": base64.b64encode(private.sign(msg)).decode()}


def verify_with(public_key_b64, envelope, domain):
    from commerce_adapter.contracts import verify
    return verify(envelope, public_key_b64, domain)


def load_public(public_key_b64):
    raw = base64.b64decode(public_key_b64, validate=True)
    require(len(raw) == 32, "ED25519_PUBLIC_KEY_LENGTH")
    return Ed25519PublicKey.from_public_bytes(raw)
