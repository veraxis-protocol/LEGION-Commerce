"""WP1 quick: cProfile of the parent side of Gateway.check (A, current)."""
import cProfile, pstats, sys, tempfile, time, io
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True
sys.pycache_prefix = str(Path(tempfile.gettempdir()) / ("legion-nopyc-" + str(time.time_ns())))
sys.path.insert(0, str(ROOT))
import commerce_bridge.qualified as qualified
from bridge_tests.fixtures import Fixture
src = qualified.QualifiedSources(ROOT / "runtime_sources", immutable=True)
try:
    with tempfile.TemporaryDirectory() as tmp:
        f = Fixture(tmp, qualified_sources=src)
        g, provider, mission, permit = f.gateway()
        with g.transaction() as db:
            for i in range(50): g.check(db, mission, permit)
            pr = cProfile.Profile(); pr.enable()
            for i in range(300): g.check(db, mission, permit)
            pr.disable()
        g.admission.close()
finally:
    src.close()
s = io.StringIO(); pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(30); print(s.getvalue())
s = io.StringIO(); pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(40); print(s.getvalue())
