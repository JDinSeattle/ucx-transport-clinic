#!/usr/bin/env python3
"""Two real processes, bounded execution, raw logs and accountable request lifetimes."""
import argparse
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import signal
import socket
import statistics
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ("src/ucp/wireup/address.c:", "src/uct/sm/self/self.c:")


def save(p, v):
    p.write_text(json.dumps(v, indent=2) + "\n")


def pct(v, q):
    return sorted(v)[max(0, math.ceil(len(v) * q) - 1)] if v else None


def stop(p):
    if p.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(p.pid, signal.SIGKILL)
    p.wait()


def environment(prefix, threshold=None):
    e = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LD_LIBRARY_PATH": str(prefix / "lib"),
         "UCX_TLS": "tcp,self", "UCX_NET_DEVICES": "lo", "UCX_LOG_LEVEL": "warn",
         "UBSAN_OPTIONS": "halt_on_error=0:print_stacktrace=0", "ASAN_OPTIONS": "detect_leaks=1:halt_on_error=1"}
    if threshold is not None:
        e["UCX_RNDV_THRESH"] = str(threshold)
    return e


def run_pair(binaries, out, *, size=1024, iters=20, warmup=5, fragment=7, mode="normal", threshold=None, cpus=None, trace=True, address_version="v1"):
    out.mkdir(parents=True, exist_ok=False)
    a, b = socket.socketpair()
    processes, streams = [], []
    started = time.monotonic()
    commands = []
    try:
        for rank, sock in enumerate((a, b)):
            meta = binaries[rank]
            cmd = [str(Path(meta["bin_dir"]) / "clinic"), str(rank), str(sock.fileno()), str(size), str(iters), str(warmup), str(fragment), "500", mode]
            if cpus:
                cmd = ["taskset", "-c", str(cpus[rank])] + cmd
            commands.append(cmd)
            stdout = (out / f"rank{rank}.stdout").open("w")
            stderr = (out / f"rank{rank}.stderr").open("w")
            streams.extend((stdout, stderr))
            processes.append(subprocess.Popen(cmd, pass_fds=(sock.fileno(),), stdout=stdout, stderr=stderr,
                                               start_new_session=True, env={**environment(Path(meta["prefix"]), threshold), "CLINIC_TRACE": "1" if trace else "0", "UCX_ADDRESS_VERSION": address_version}))
        a.close(); b.close()
        deadline = time.monotonic() + 25
        timed_out = False
        for proc in processes:
            try:
                proc.wait(timeout=max(.01, deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
                break
    finally:
        a.close(); b.close()
        for p in processes:
            stop(p)
        for s in streams:
            s.close()
    results, latencies, scope_reports, other_reports = [], [], [], []
    for rank in range(2):
        for line in (out / f"rank{rank}.stdout").read_text().splitlines():
            if not line.startswith("{"):
                continue
            obj = json.loads(line)
            if "result" in obj: results.append(obj)
            if "roundtrip_ns" in obj: latencies.append(obj["roundtrip_ns"])
        err = (out / f"rank{rank}.stderr").read_text()
        for line in err.splitlines():
            if "runtime error:" in line:
                (scope_reports if any(s in line for s in SCOPE) else other_reports).append(line)
        if "ERROR: AddressSanitizer" in err or "ERROR: LeakSanitizer" in err:
            other_reports.append("ASAN/LSAN error in rank " + str(rank))
    expected_codes = [0, 42] if mode == "peer-exit" else [0, 0]
    expected_results = 1 if mode == "peer-exit" else 2
    passed = not timed_out and [p.returncode for p in processes] == expected_codes and len(results) == expected_results
    passed &= all(r["result"] == "pass" and r["allocated"] == r["completed"] == r["freed"] for r in results)
    if mode == "normal": passed &= len(latencies) == iters
    if mode != "normal": passed &= all(r["cancelled"] >= 1 for r in results)
    if any(b["variant"].endswith("asan") for b in binaries): passed &= not other_reports
    record = {"pass": bool(passed), "timed_out": timed_out, "commands": commands,
              "variants": [b["variant"] for b in binaries], "mode": mode, "size": size,
              "fragment": fragment, "rendezvous_threshold": threshold, "trace": trace, "address_version": address_version,
              "cpu_affinity": cpus, "env": environment(Path(binaries[0]["prefix"]), threshold),
              "returncodes": [p.returncode for p in processes], "elapsed_s": time.monotonic()-started,
              "lifetimes": results, "scope_alignment_reports": scope_reports,
              "other_sanitizer_reports": other_reports,
              "roundtrip_ns": {"n": len(latencies), "p50": statistics.median(latencies) if latencies else None,
                               "p95": pct(latencies, .95), "p99": pct(latencies, .99)},
              "raw_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.glob("rank*")}}
    save(out / "result.json", record)
    return record


def hello_pair(meta, out):
    """Unmodified upstream example; no custom wireup or crafted alignment."""
    out.mkdir(parents=True, exist_ok=False)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    streams, procs = [], []
    try:
        for rank in range(2):
            cmd = [str(Path(meta["bin_dir"]) / "hello"), "-p", str(port)]
            if rank: cmd += ["-n", "127.0.0.1"]
            stdout = (out / f"rank{rank}.stdout").open("w"); stderr = (out / f"rank{rank}.stderr").open("w")
            streams.extend((stdout, stderr))
            procs.append(subprocess.Popen(cmd, stdout=stdout, stderr=stderr, start_new_session=True,
                                           env=environment(Path(meta["prefix"]))))
            if rank == 0:
                # Inspect the Linux listen table without consuming the example's sole accept().
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    listening = any(f":{port:04X}" in line and line.split()[3] == "0A"
                                    for line in Path("/proc/net/tcp").read_text().splitlines()[1:])
                    if listening: break
                    if procs[0].poll() is not None: break
                    time.sleep(.01)
        timed_out = False
        for proc in procs:
            try: proc.wait(timeout=15)
            except subprocess.TimeoutExpired: timed_out = True; break
    finally:
        for proc in procs: stop(proc)
        for stream in streams: stream.close()
    err = "\n".join(p.read_text() for p in out.glob("*.stderr"))
    reports = [s for s in err.splitlines() if "runtime error:" in s and any(x in s for x in SCOPE)]
    success_marker = any("UCP TEST SUCCESS" in p.read_text() for p in out.glob("*.stdout"))
    r = {"variant": meta["variant"], "returncodes": [p.returncode for p in procs],
         "success_marker": success_marker, "timed_out": timed_out, "scope_reports": reports,
         "other_reports": [s for s in err.splitlines() if "runtime error:" in s and not any(x in s for x in SCOPE)]}
    r["pass"] = r["returncodes"] == [0,0] and success_marker and not timed_out
    save(out / "result.json", r)
    return r


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--variants", nargs="+", default=["stock-align", "patched-align"])
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--hello-only", action="store_true")
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    metas = [json.loads((ROOT / f"evidence/local/builds/{v}.json").read_text()) for v in a.variants]
    save(a.out / "manifest.json", {"platform": platform.platform(), "cpu_affinity": sorted(os.sched_getaffinity(0)), "builds": metas,
                                  "scope": "CPU TCP loopback; socketpair bootstrap only; no RDMA/GPU claims"})
    results = []
    for meta in metas:
        r = hello_pair(meta, a.out / (meta["variant"] + "-hello"))
        if meta["variant"] == "stock-align": r["acceptance"] = r["pass"] and bool(r["scope_reports"])
        elif meta["variant"] == "patched-align": r["acceptance"] = r["pass"] and not r["scope_reports"]
        else: r["acceptance"] = r["pass"]
        results.append(r)
        if a.hello_only: continue
        for i, (size, fragment, mode, threshold) in enumerate([
            (1, 1, "normal", None), (65537, 7, "normal", "inf"),
            (1048576, 4096, "normal", "0"), (1024, 3, "cancel", None),
            (1024, 5, "peer-exit", None)]):
            r = run_pair([meta, meta], a.out / f"{meta['variant']}-{i}", size=size, fragment=fragment, mode=mode, threshold=threshold)
            r["acceptance"] = r["pass"] and (not r["scope_alignment_reports"] if meta["variant"].startswith("patched") else True)
            results.append(r)
    if len(metas) == 2 and not a.hello_only:
        for i, pair in enumerate((metas, list(reversed(metas)))):
            r = run_pair(pair, a.out / f"mixed-version-{i}", size=65537, fragment=1)
            r["acceptance"] = r["pass"]
            results.append(r)
    save(a.out / "summary.json", {"pass": all(r["acceptance"] for r in results), "cases": results})
    print(json.dumps({"pass": all(r["acceptance"] for r in results), "cases": len(results), "out": str(a.out)}, indent=2))
    return 0 if all(r["acceptance"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
