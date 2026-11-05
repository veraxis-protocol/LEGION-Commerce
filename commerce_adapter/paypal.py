"""Windows-only credential owner. No secrets in CLI arguments, output or ledger."""
import base64
import ctypes
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from ctypes import wintypes
from .contracts import Denied, canonical, require
from .gateway import ProviderError

BASE = "https://api-m.sandbox.paypal.com"


def credential_dir():
    require(os.name == "nt", "WINDOWS_HOST_REQUIRED")
    return Path(os.environ["LOCALAPPDATA"]) / "Veraxis" / "LEGION-Commerce"


def load_credentials():
    root = credential_dir()
    # PowerShell Set-Content can write UTF-16 BOM or UTF-8/ASCII; .Trim() is
    # essential: ConvertFrom-SecureString output is hex with trailing CRLF.
    def text(path):
        raw = path.read_bytes()
        return raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig").strip()
    client_id = text(root / "paypal_client_id.txt")
    encrypted = bytes.fromhex(text(root / "paypal_client_secret.dpapi"))
    require(bool(client_id) and len(encrypted) > 20, "CREDENTIAL_FORMAT")
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buf = (ctypes.c_ubyte * len(encrypted)).from_buffer_copy(encrypted)
    incoming, outgoing = Blob(len(encrypted), buf), Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt.CryptUnprotectData.restype = wintypes.BOOL
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    require(bool(crypt.CryptUnprotectData(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing))), "DPAPI_DECRYPT_FAILED")
    try:
        secret = ctypes.string_at(outgoing.data, outgoing.size).decode("utf-16-le")
    finally:
        ctypes.memset(outgoing.data, 0, outgoing.size)
        kernel.LocalFree(outgoing.data)
    return client_id, secret


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PayPalSandbox:
    evidence_mode = "PAYPAL_SANDBOX_AUTHENTICATED_GET"

    def __init__(self, read_only=True, credential_loader=load_credentials, clock=time.monotonic):
        require(os.name == "nt", "WINDOWS_HOST_REQUIRED")
        self.read_only, self.loader, self.clock = read_only, credential_loader, clock
        self._token = None
        self._expires = 0
        # Disable environment proxies: secrets must reach the fixed PayPal TLS
        # origin directly. No redirects, alternate endpoints or TLS overrides.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                      urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def _wire(self, method, path, data, headers):
        req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=20) as response:
                raw = response.read(1024 * 1024 + 1)
                require(len(raw) <= 1024 * 1024, "PROVIDER_RESPONSE_TOO_LARGE")
                return response.status, json.loads(raw)
        except urllib.error.HTTPError as error:
            # Never echo raw error bodies, request headers, payer data or URL.
            code = "HTTP_REJECTED" if 400 <= error.code < 500 else "HTTP_UNCERTAIN"
            def safe_symbol(value):
                return value if isinstance(value,str) and re.fullmatch(r"[A-Z0-9_]{1,80}",value) else None
            name, issues, debug = None, [], None
            try:
                info=json.loads(error.read(65536))
                name=safe_symbol(info.get("name"))
                issues=[issue for detail in info.get("details",[]) if (issue:=safe_symbol(detail.get("issue")))]
                candidate=info.get("debug_id")
                if isinstance(candidate,str) and re.fullmatch(r"[a-fA-F0-9-]{1,64}",candidate): debug=candidate
            except (ValueError,TypeError,AttributeError,OSError):
                pass
            raise ProviderError(error.code, code, name, issues, debug) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
            raise ProviderError(0, "NETWORK_UNCERTAIN") from None
        except (json.JSONDecodeError, UnicodeError, Denied):
            raise ProviderError(0, "MALFORMED_RESPONSE") from None

    def oauth(self):
        if self._token and self.clock() < self._expires:
            return self._token
        try:
            client_id, secret = self.loader()
            basic = base64.b64encode((client_id + ":" + secret).encode()).decode()
            status, body = self._wire("POST", "/v1/oauth2/token", b"grant_type=client_credentials",
                                     {"Authorization": "Basic " + basic, "Content-Type": "application/x-www-form-urlencoded"})
            require(status == 200 and body["token_type"].lower() == "bearer"
                    and body["access_token"] and type(body["expires_in"]) is int and body["expires_in"] > 60, "INVALID_OAUTH")
            self._token = body["access_token"]
            self._expires = self.clock() + body["expires_in"] - 60
            self.ttl = body["expires_in"]
            return self._token
        except (KeyError, ValueError, OSError, Denied):
            raise Denied("CREDENTIAL_OR_OAUTH_FAILURE") from None
        finally:
            client_id = secret = basic = body = None

    def request(self, method, path, body=None, request_id=None):
        allowed = ((method == "GET" and re.fullmatch(r"/v2/(checkout/orders|payments/captures)/[A-Z0-9]{1,32}", path))
                   or (method == "POST" and (path == "/v2/checkout/orders"
                       or re.fullmatch(r"/v2/checkout/orders/[A-Z0-9]{1,32}/capture", path))))
        require(allowed, "UNSUPPORTED_PAYPAL_OPERATION")
        require(method == "GET" or not self.read_only, "READ_ONLY_CLIENT")
        headers = {"Authorization": "Bearer " + self.oauth(), "Content-Type": "application/json", "Prefer": "return=representation"}
        if method == "POST":
            require(request_id is not None and re.fullmatch(r"[a-f0-9-]{36}", request_id), "REQUEST_ID_REQUIRED")
            headers["PayPal-Request-Id"] = request_id
        try:
            return self._wire(method, path, canonical(body) if body is not None else None, headers)
        except ProviderError as error:
            if error.status == 401:
                self._token = None
                self._expires = 0
            # No automatic POST retry, including OAuth expiry or HTTP 5xx.
            raise
