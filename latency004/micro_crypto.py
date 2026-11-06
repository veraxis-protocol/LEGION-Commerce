import sys, time, tempfile, json, statistics, base64
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
import commerce_bridge.qualified as qualified
from commerce_adapter.contracts import canonical, verify
from bridge_tests.fixtures import Fixture
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey, Ed25519PrivateKey
import cryptography
def t(fn, n=2000):
    for _ in range(100): fn()
    xs=[]
    for _ in range(n):
        a=time.perf_counter_ns(); fn(); xs.append((time.perf_counter_ns()-a)/1e3)
    return round(statistics.median(xs),1)
src = qualified.QualifiedSources(ROOT / "runtime_sources", immutable=True)
with tempfile.TemporaryDirectory() as tmp:
    f = Fixture(tmp, qualified_sources=src)
    b = f.bundle()
    for k,v in b.items():
        msg=canonical(v["payload"])
        print(k, len(msg), "canonical_us", t(lambda: canonical(v["payload"])), "dumps_only_us", t(lambda: json.dumps(v["payload"],sort_keys=True,separators=(",",":"),ensure_ascii=True)))
    k=Ed25519PrivateKey.generate(); pk=k.public_key(); m=b"x"*600; s=k.sign(m)
    raw=pk.public_bytes_raw()
    print("ed25519 verify us", t(lambda: pk.verify(s,m)), "from_public_bytes us", t(lambda: Ed25519PublicKey.from_public_bytes(raw)))
    m2=b"x"*6000; s2=k.sign(m2); print("verify 6KB us", t(lambda: pk.verify(s2,m2)))
    print("cryptography", cryptography.__version__)
src.close()
