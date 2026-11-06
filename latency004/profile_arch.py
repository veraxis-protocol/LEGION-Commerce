"""WO-COM-LATENCY-004 WP1/WP2: instrumented decomposition of the full gate for one architecture.

Same boundary and method as profile_wp1.py (exclusive wall + cycle-precise CPU per component,
QPC stamps from profiling shims around the unmodified workers). For B/BC the worker's
timeline (transit, parse, Core, decider||witness, serialize, return transit) is recorded as
"timeline.*" (it overlaps parent work under BC, so it is not added to the exclusive sum).

Usage: python latency004/profile_arch.py <out_dir> <A|B|C|BC> <repeat|fresh> [N=1000] [warmup=100]
"""
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import profile_wp1 as pw  # noqa: E402  (instrumentation primitives; imports the code under test)
from arch import Fixture, build_gateway, sources_for  # noqa: E402
from bench import fresh_permits  # noqa: E402
import commerce_bridge.prepared as prepared  # noqa: E402
import commerce_bridge.consolidated as consolidated  # noqa: E402
import commerce_bridge.transport as transport  # noqa: E402
import commerce_bridge.qualified as qualified  # noqa: E402

pc, tc = pw.pc, pw.tc
SEND = {}


class KeyProxy:
    def __init__(self, key): self.key = key

    def verify(self, sig, msg):
        w, c = pc(), tc()
        try: return self.key.verify(sig, msg)
        finally: pw.leaf("auth.ed25519_verify", pc() - w, tc() - c)


def install_prepared():
    P = prepared.PreparedAdmission
    for name, label in (("control", "authority.control_predicates"), ("ready", "authority.readiness_predicates"),
                        ("_check", "authority.a04_and_bundle_predicates"), ("policy", "bridge.policy_predicates"),
                        ("assess", "bridge.assess_pre_dispatch"), ("_assess_rest", "bridge.assess_post_dispatch"),
                        ("verify", "auth.verify_memo_glue"), ("canon", "canonicalize"), ("digest", "digest.sha256_glue")):
        pw.wrap(P, name, label)
    orig_key = P._key
    P._key = lambda self, pk: KeyProxy(orig_key(self, pk))


def install_consolidated():
    S = consolidated.ConsolidatedSession
    pw.wrap(S, "dispatch", "core.dispatch_serialize_write")
    orig_collect = S.collect

    def collect(self, ticket):
        pw.push("core.collect_parse_glue")
        try:
            result = orig_collect(self, ticket)
        finally:
            pw.pop()
        prof = result.pop("_prof", None)
        if prof and pw.REQ is not None:
            t_written, t_got, t_wait0 = SEND.get("written", 0), SEND.get("got", 0), SEND.get("wait0", 0)
            core = prof["t_core1"] - prof["t_core0"]
            parts = {"transit_and_wake_to_worker": prof["t_recv"] - t_written, "worker_json_parse": prof["t_parsed"] - prof["t_recv"],
                     "worker_core_alpha_oracle": core, "worker_decider_and_witness_wait": prof["child_wait_ns"],
                     "worker_checks_glue": (prof["t_done"] - prof["t_parsed"]) - core - prof["child_wait_ns"],
                     "worker_serialize": prof["t_ser"] - prof["t_done"], "transit_and_wake_to_parent": t_got - prof["t_ser"],
                     "parent_work_overlapped_after_dispatch": t_wait0 - t_written, "worker_critical_path_dispatch_to_answer": t_got - t_written}
            for k, v in parts.items(): pw.add("timeline." + k, v, 0)
            pw.add("timeline.worker_cpu", prof["cpu_ns"], 0)
        return result
    S.collect = collect
    C = transport.Child
    orig_send, orig_recv = C.send, C.receive

    def send(self, line):
        try: return orig_send(self, line)
        finally: SEND["written"] = pc()

    def receive(self):
        SEND["wait0"] = w = pc(); c = tc()
        try: return orig_recv(self)
        finally:
            SEND["got"] = pc(); pw.leaf("core.worker.exposed_wait", SEND["got"] - w, tc() - c)
    C.send, C.receive = send, receive


def attach_shims(arch, src):
    if arch in ("A", "C"):  # (spin variants exist only for B/BC)
        src.session.worker.close()
        src.session.worker = qualified.JsonProcess([sys.executable, "-B", HERE / "prof_worker.py", src.core, src.root / "ztl-swarm"], timeout=5)
        src.session.worker._tag = "core.worker"; src.session.lean._tag = "lean.legion_decide"
    else:
        s = src.session
        s.child.command[2] = str(HERE / "prof_consolidated_worker.py")
        s.child.restart()


def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    arch, proto = sys.argv[2], sys.argv[3]
    n = int(sys.argv[4]) if len(sys.argv) > 4 else 1000
    warm = int(sys.argv[5]) if len(sys.argv) > 5 else 100
    host0 = pw.host_state()
    pw.install()                      # A-path primitives (used by A, B parents and A/C sessions)
    install_prepared(); install_consolidated()
    src = sources_for(arch)
    per = []; totals = []
    try:
        attach_shims(arch, src)
        with tempfile.TemporaryDirectory() as tmp:
            f = Fixture(tmp + "/fx", qualified_sources=src)
            gw = build_gateway(f, tmp + "/g", arch, src)
            g, _, m, pe = gw
            fresh = fresh_permits(f, n + warm, {arch: gw}) if proto == "fresh" else None
            with g.transaction() as db:
                for i in range(warm + n):
                    permit = pe if fresh is None else fresh[i][arch]
                    pw.REQ = {}; pw.IPC.clear()
                    w, c = pc(), tc()
                    g.check(db, m, permit)
                    tw, tcpu = pc() - w, tc() - c
                    if i >= warm:
                        totals.append(tw); per.append({"total": tw, "cpu": tcpu, "c": pw.REQ})
                pw.REQ = None
            stats_obj = getattr(g.admission, "stats", None)
            g.admission.close()
    finally:
        src.close()
    comps = sorted({k for r in per for k in r["c"]})
    table = {}
    for k in comps:
        wall = [r["c"].get(k, [0, 0, 0])[0] for r in per]
        table[k] = {"wall": pw.stats(wall), "cpu_mean_ms": round(statistics.mean(r["c"].get(k, [0, 0, 0])[1] for r in per) / 1e6, 4),
                    "calls_per_request": round(statistics.mean(r["c"].get(k, [0, 0, 0])[2] for r in per), 3)}
    excl = [k for k in comps if not k.startswith(("timeline.", "python.gc"))]
    recon = [sum(r["c"][k][0] for k in excl if k in r["c"]) for r in per]
    res = {"arch": arch, "protocol": proto, "N": n, "warmup": warm, "end_to_end": pw.stats(totals),
           "end_to_end_main_thread_cpu": pw.stats([r["cpu"] for r in per]),
           "reconstruction": {"sum_of_exclusive_components": pw.stats(recon),
                              "unattributed_residual": pw.stats([r["total"] - x for r, x in zip(per, recon)]),
                              "explained_fraction_of_mean": round(sum(recon) / sum(totals), 4)},
           "components": table, "memo_stats": stats_obj, "host_start": host0, "host_end": pw.host_state(),
           "gc": {"pauses": pw.GC["n"], "pause_ms_total": pw.GC["ns"] / 1e6},
           "git_head": os.popen('git -C "%s" rev-parse HEAD' % HERE.parent).read().strip()}
    (out / f"PROFILE_{arch}_{proto}.json").write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    with (out / f"RAW_{arch}_{proto}.jsonl").open("w") as fh:
        for r in per: fh.write(json.dumps({"total_ns": r["total"], "cpu_ns": r["cpu"], "components": r["c"]}) + "\n")
    print(arch, proto, "e2e", res["end_to_end"], "explained", res["reconstruction"]["explained_fraction_of_mean"],
          "speed", host0["python_speed_index_ms_2e6_loop"])
    for k, v in sorted(table.items(), key=lambda kv: -kv[1]["wall"]["mean"]):
        print(f"  {k:58} mean {v['wall']['mean']:8.4f} p95 {v['wall']['p95']:8.4f} p99 {v['wall']['p99']:8.4f} cpu {v['cpu_mean_ms']:7.4f} x{v['calls_per_request']}")


if __name__ == "__main__":
    main()

