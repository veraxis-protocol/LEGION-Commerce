"""WP1: measure the exact Core file closure opened by the qualified v2 worker.

Runs the unchanged qualified_worker.qualified() under the same frozen import
finder on recorded alpha inputs (every category), with a Python audit hook that
records every file opened. Output: hash-only inventory. Never copies Core text.

Usage: python tools/execution_closure.py <alpha-inputs.jsonl> <output.json>
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = (ROOT / "runtime_sources/LEGION_QUALIFIED").resolve()
SWARM = (ROOT / "runtime_sources/ztl-swarm").resolve()
OLD = "d083c44af2526e38068e0220891cd0bb5fce52af"
NEW = "d9d38cd6fd9e93e002b0b5d5135377ce1d33d8c8"

opened = set()


def hook(event, args):
    if event == "open" and args and isinstance(args[0], (str, bytes)):
        p = args[0].decode() if isinstance(args[0], bytes) else args[0]
        opened.add(p)


def git(*args):
    return subprocess.run(["git", "-C", str(CORE), *args], capture_output=True, check=True).stdout


def blob(pin, path):
    r = subprocess.run(["git", "-C", str(CORE), "rev-parse", f"{pin}:{path}"], capture_output=True)
    return r.stdout.decode().strip() if r.returncode == 0 else None


def main():
    inputs = [json.loads(l) for l in Path(sys.argv[1]).read_text().splitlines()]
    per_category = {}
    for row in inputs:
        per_category.setdefault(row["category"], row)
    sys.path.insert(0, str(ROOT / "commerce_bridge"))
    import qualified_worker as qw
    qw.frozen_imports(str(CORE), str(SWARM))
    sys.addaudithook(hook)
    verdicts = {}
    for row in inputs:  # every recorded input, not one per category
        data = {"observation": row["observation"], "policy": row["policy"], "now": row["now"]}
        result = qw.qualified(data, str(CORE), str(SWARM))
        verdicts.setdefault(row["category"], set()).add(result["qualified_runtime"]["verdict"])
    verdicts = {k: sorted(v) for k, v in sorted(verdicts.items())}
    core_files = set()
    for p in opened:
        try:
            rel = Path(p).resolve().relative_to(CORE).as_posix()
        except (ValueError, OSError):
            continue
        if (CORE / rel).is_file():
            core_files.add(rel)
    swarm_files = set()
    for p in opened:
        try:
            swarm_files.add(Path(p).resolve().relative_to(SWARM).as_posix())
        except (ValueError, OSError):
            pass
    rows = []
    for rel in sorted(core_files):
        o, n = blob(OLD, rel), blob(NEW, rel)
        raw = (CORE / rel).read_bytes()
        rows.append({"path": rel, "sha256": hashlib.sha256(raw).hexdigest(),
                     "git_blob_matches_qualified": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == n,
                     "old_blob": o, "new_blob": n,
                     "delta": "UNCHANGED" if o == n else ("ADDED_IN_QUALIFIED" if o is None else "MODIFIED")})
    out = {"schema": "commerce.wp1.execution-closure.v1", "old": OLD, "qualified": NEW,
           "categories_executed": sorted(per_category), "runtime_verdicts": verdicts,
           "python": sys.version, "core_files_opened": rows,
           "swarm_files_opened": sorted(swarm_files),
           "all_opened_core_bytes_equal_qualified_commit": all(r["git_blob_matches_qualified"] for r in rows)}
    Path(sys.argv[2]).write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"core_files": len(rows), "changed": [r["path"] for r in rows if r["delta"] != "UNCHANGED"],
                      "bytes_ok": out["all_opened_core_bytes_equal_qualified_commit"], "verdicts": verdicts}, indent=1))


if __name__ == "__main__":
    main()
