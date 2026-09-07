#!/usr/bin/env python3
"""Build a pinned CPU-only UCX, without touching system installations."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REV = "bade18369e5245db37babf05ffbd88b1f5321a25"
VARIANTS = ("stock-align", "patched-align", "stock-release", "patched-release", "patched-asan")


def default_work():
    return Path(tempfile.gettempdir()) / ("ucx-clinic-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:10])


def run(cmd, cwd, log, env=None):
    log.write("$ " + repr(cmd) + "\n")
    log.flush()
    subprocess.run(cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("variant", choices=VARIANTS)
    p.add_argument("--source", type=Path)
    p.add_argument("--work-dir", type=Path, default=default_work())
    p.add_argument("--jobs", type=int, default=6)
    a = p.parse_args()
    work = a.work_dir.resolve()
    if any(c.isspace() for c in str(work)):
        p.error("UCX Autotools requires a work directory without whitespace")
    work.mkdir(parents=True, exist_ok=True)
    evidence = ROOT / "evidence" / "local" / "builds"
    evidence.mkdir(parents=True, exist_ok=True)
    with (evidence / (a.variant + ".log")).open("w") as log:
        src = work / "source"
        if not src.exists():
            if a.source:
                actual = subprocess.check_output(["git", "-C", str(a.source), "rev-parse", "HEAD"], text=True).strip()
                if actual != REV:
                    raise ValueError(f"source revision {actual} != {REV}")
                shutil.copytree(a.source, src, symlinks=True, ignore=shutil.ignore_patterns(".git", "autom4te.cache"))
            else:
                run(["git", "init", str(src)], work, log)
                run(["git", "fetch", "--depth=1", "https://github.com/openucx/ucx.git", REV], src, log)
                run(["git", "checkout", "--detach", "FETCH_HEAD"], src, log)
            if not (src / "configure").exists():
                run(["./autogen.sh"], src, log)
        variant_src = work / (a.variant + "-src")
        if not variant_src.exists():
            shutil.copytree(src, variant_src, symlinks=True, ignore=shutil.ignore_patterns(".git", "autom4te.cache"))
        if a.variant.startswith("patched"):
            for patch in sorted((ROOT / "patches").glob("*.patch")):
                check = subprocess.run(["patch", "--batch", "--forward", "--dry-run", "-p1", "-i", str(patch)], cwd=variant_src, capture_output=True)
                if check.returncode == 0:
                    run(["patch", "--batch", "--forward", "-p1", "-i", str(patch)], variant_src, log)
                else:
                    run(["patch", "--batch", "--reverse", "--dry-run", "-p1", "-i", str(patch)], variant_src, log)
        build = work / a.variant
        prefix = work / (a.variant + "-install")
        build.mkdir(exist_ok=True)
        flags = "-g -O2"
        ldflags = ""
        if a.variant.endswith("align"):
            flags += " -fsanitize=alignment"
            ldflags = "-fsanitize=alignment"
        elif a.variant.endswith("asan"):
            flags += " -fsanitize=address -fno-omit-frame-pointer"
            ldflags = "-fsanitize=address"
        else:
            flags = "-g -O3 -DNDEBUG"
        warning = "-Wno-error=default-const-init-var-unsafe"
        probe = subprocess.run(["clang", "-x", "c", "-Werror", warning, "-fsyntax-only", "-"], input="", text=True, capture_output=True)
        if probe.returncode == 0:
            flags += " " + warning
        cmd = [str(variant_src / "configure"), "CC=clang", "CXX=clang++",
               f"CFLAGS={flags}", f"CXXFLAGS={flags}", f"LDFLAGS={ldflags}",
               f"--prefix={prefix}", "--without-cuda", "--without-rocm", "--without-ze",
               "--without-verbs", "--without-knem", "--without-xpmem", "--disable-cma",
               "--without-gdrcopy", "--without-java", "--without-go"]
        stamp = build / "clinic-configure.json"
        if not stamp.exists() or json.loads(stamp.read_text()) != cmd:
            run(cmd, build, log)
            stamp.write_text(json.dumps(cmd))
        run(["make", f"-j{a.jobs}"], build, log)
        run(["make", "install"], build, log)
        bin_dir = build / "clinic-bin"
        bin_dir.mkdir(exist_ok=True)
        common = ["clang", *flags.split(), "-I" + str(prefix / "include"),
                  "-L" + str(prefix / "lib"), "-Wl,-rpath," + str(prefix / "lib")]
        run(common + [str(variant_src / "examples/ucp_hello_world.c"), "-lucp", "-lucs", "-lrt",
                      "-o", str(bin_dir / "hello")], build, log)
        if (ROOT / "src/clinic.c").exists():
            run(common + ["-Wall", "-Wextra", "-Werror", str(ROOT / "src/clinic.c"), "-lucp", "-lucs",
                          "-o", str(bin_dir / "clinic")], build, log)
        fields = ROOT / "tests/address_fields.c"
        if fields.exists():
            run(common + ["-DHAVE_CONFIG_H", "-I" + str(build), "-I" + str(variant_src / "src"),
                          "-I" + str(build / "src"), str(fields), "-lucp", "-lucs", "-luct",
                          "-o", str(bin_dir / "address_fields")], build, log)
        meta = {"revision": REV, "variant": a.variant, "prefix": str(prefix),
                "bin_dir": str(bin_dir), "configure": cmd,
                "compiler": subprocess.check_output(["clang", "--version"], text=True).splitlines()[0]}
        (evidence / (a.variant + ".json")).write_text(json.dumps(meta, indent=2) + "\n")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
