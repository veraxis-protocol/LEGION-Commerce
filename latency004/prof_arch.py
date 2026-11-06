import cProfile, pstats, io, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from arch import Fixture, build_gateway, sources_for
arch = sys.argv[1] if len(sys.argv) > 1 else "BC"
src = sources_for(arch)
with tempfile.TemporaryDirectory() as tmp:
    f = Fixture(tmp + "/fx", qualified_sources=src)
    g, p, m, pe = build_gateway(f, tmp + "/g", arch, src)
    with g.transaction() as db:
        for i in range(50): g.check(db, m, pe)
        pr = cProfile.Profile(); pr.enable()
        for i in range(300): g.check(db, m, pe)
        pr.disable()
    g.admission.close()
src.close()
s = io.StringIO(); pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(25); print(s.getvalue()[:6000])
