"""Local profile of complete admission, with temporary test authority only."""
import cProfile
import io
import json
import pstats
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from commerce_bridge.qualified import QualifiedSources
from bridge_tests.fixtures import Fixture

sources=QualifiedSources(ROOT/"runtime_sources",immutable=True)
try:
    with tempfile.TemporaryDirectory() as temp:
        f=Fixture(temp,sources); g,p,m,permit=f.gateway()
        with g.transaction() as db:
            g.check(db,m,permit)
            profile=cProfile.Profile(); profile.enable()
            for _ in range(100): g.check(db,m,permit)
            profile.disable()
        stream=io.StringIO(); pstats.Stats(profile,stream=stream).strip_dirs().sort_stats("cumtime").print_stats(35)
        (ROOT/"qualify_evidence/FULL_ADMISSION_PROFILE.txt").write_text(stream.getvalue(),encoding="utf-8")
        print(stream.getvalue())
finally: sources.close()
