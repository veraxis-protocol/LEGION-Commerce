"""WP3 experiment: is the first-open penalty of a new bundle file paid once per file (absorbable at issuance)?"""
import json, statistics, sys, tempfile, time
from pathlib import Path
def rd(p):
    t=time.perf_counter_ns(); p.is_file(); json.loads(p.read_text(encoding="utf-8-sig")); return (time.perf_counter_ns()-t)/1e6
q=lambda v: {"p50":round(statistics.median(v),3),"p95":round(sorted(v)[int(.95*len(v))-1],3),"max":round(max(v),3)}
data=json.dumps({"x":"y"*6000}); res={}
for mode in ("no_readback","writer_readback"):
    d=Path(tempfile.mkdtemp())
    for i in range(400):
        p=d/f"{i}.json"; p.write_text(data)
        if mode=="writer_readback": p.read_bytes()
    time.sleep(1)
    res[mode+"_gateway_first_open"]=q([rd(d/f"{i}.json") for i in range(400)])
    res[mode+"_gateway_second_open"]=q([rd(d/f"{i}.json") for i in range(400)])
print(json.dumps(res,indent=1))
if len(sys.argv)>1: Path(sys.argv[1]).write_text(json.dumps(res,indent=1)+"\n")
