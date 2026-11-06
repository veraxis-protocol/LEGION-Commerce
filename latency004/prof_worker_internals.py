import cProfile, pstats, io, sys, tempfile, time, json
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True
sys.pycache_prefix = str(Path(tempfile.gettempdir()) / ("legion-nopyc-" + str(time.time_ns())))
sys.path.insert(0, str(ROOT))
import commerce_bridge.qualified as qualified
from bridge_tests.fixtures import Fixture
src = qualified.QualifiedSources(ROOT / "runtime_sources", immutable=True)
with tempfile.TemporaryDirectory() as tmp:
    f = Fixture(tmp, qualified_sources=src)
    b = f.bundle()
    data = {"observation": b["observation"]["payload"], "policy": b["policy"]["payload"], "now": f.now}
src.close()
CORE = str(ROOT / "runtime_sources/LEGION_QUALIFIED"); SWARM = str(ROOT / "runtime_sources/ztl-swarm")
sys.path.insert(0, str(ROOT / "commerce_bridge"))
import qualified_worker as qw
qw.frozen_imports(CORE, SWARM)
line = json.dumps(data)
for _ in range(30): qw.qualified(json.loads(line), CORE, SWARM)
xs=[]
for _ in range(200):
    t=time.perf_counter_ns(); qw.qualified(json.loads(line), CORE, SWARM); xs.append((time.perf_counter_ns()-t)/1e6)
xs.sort(); print("inproc qualified p50", xs[100], "p95", xs[190])
pr = cProfile.Profile(); pr.enable()
for _ in range(200): qw.qualified(json.loads(line), CORE, SWARM)
pr.disable()
s = io.StringIO(); pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(45); print(s.getvalue()[:9000])
