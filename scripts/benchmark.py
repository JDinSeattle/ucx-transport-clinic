#!/usr/bin/env python3
"""Alternating, affinity-controlled release comparison with request tracing disabled."""
import argparse
import json
from pathlib import Path
import random
import statistics
from validate import ROOT, run_pair, save


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--repeats",type=int,default=7)
    p.add_argument("--iterations",type=int,default=200)
    p.add_argument("--cpus",nargs=2,type=int,default=[2,4])
    a=p.parse_args()
    if a.repeats<3 or a.iterations<10:p.error("at least 3 repeats and 10 iterations")
    a.out.mkdir(parents=True,exist_ok=False)
    metas={v:json.loads((ROOT/f"evidence/local/builds/{v}.json").read_text()) for v in ("stock-release","patched-release")}
    records=[]
    for rep in range(a.repeats):
        versions=list(metas) if rep%2==0 else list(reversed(metas))
        for size in (64,4096,1048576):
            for variant in versions:
                meta=metas[variant]
                r=run_pair([meta,meta],a.out/f"r{rep}-{variant}-{size}",size=size,iters=a.iterations,warmup=30,cpus=a.cpus,trace=False)
                if not r["pass"]:raise RuntimeError("correctness gate failed; discard comparison")
                r["repeat"]=rep;records.append(r)
    comparisons=[];rng=random.Random(7)
    for size in (64,4096,1048576):
        ratios=[]
        for rep in range(a.repeats):
            pair={r["variants"][0]:r for r in records if r["repeat"]==rep and r["size"]==size}
            ratios.append(pair["patched-release"]["roundtrip_ns"]["p50"]/pair["stock-release"]["roundtrip_ns"]["p50"])
        boot=sorted(statistics.median(rng.choices(ratios,k=len(ratios))) for _ in range(5000))
        lo,hi=boot[124],boot[4874]
        comparisons.append({"bytes":size,"paired_p50_ratios_patched_over_stock":ratios,
                            "median_ratio":statistics.median(ratios),"bootstrap_median_95pct_ci":[lo,hi],
                            "assessment":"regression_needs_investigation" if lo>1.10 else "no_resolved_over_10pct_regression",
                            "uncertain_over_10pct":hi>1.10})
    save(a.out/"summary.json",{"pass":True,"comparisons":comparisons,"cpus":a.cpus,"iterations":a.iterations,"repeats":a.repeats,
                               "timing":"application ping-pong round trip; not one-way wire latency; no sanitizer; trace disabled",
                               "limitation":"loopback, shared workstation and small repeat count; intervals describe these trials only"})
    print(json.dumps(comparisons,indent=2))


if __name__=="__main__":main()
