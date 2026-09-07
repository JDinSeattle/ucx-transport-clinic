# UCX Transport Clinic — paused checkpoint

This project is paused at the user's request. The existing implementation, candidate patches, raw logs and original specification are preserved. It is **not marked complete**.

The intended work is a reproducible CPU-only study of UCP address serialization and asynchronous request ownership, pinned to UCX `bade18369e5245db37babf05ffbd88b1f5321a25`. See [the original scope](PROJECT_SPEC.md) and [the checkpoint](CHECKPOINT.md).

## Evidence already obtained

| Experiment | Observed result |
|---|---|
| Unmodified upstream hello-world, TCP/self | Both processes transferred successfully; 12 target alignment reports |
| Stock/patched alignment integration matrix | 14 cases passed their scoped gates, including mixed-version endpoints |
| Candidate address patch | Target wireup/self alignment reports fell from 12 to 0 in homogeneous runs |
| ASan/LSan custom UCP regression | Data, cancellation and controlled peer-exit cases passed |
| Additional upstream example leak | 77-byte peer-address leak reproduced; one-line candidate fix passed six ASan/LSan cases |
| Release latency comparison | Measurements saved; variability prevents a broad no-regression conclusion |

The full alignment logs contain **other UCX alignment reports outside the candidate patch's scope**. This is not a claim that all UCX sanitizer findings were fixed. Loopback measurements do not establish physical-network or RDMA performance.

## Outstanding at pause

- `tests/address_fields.c` currently includes a nonexistent `ucs/sys/mem.h`; the newly added internal-API unit target therefore fails to compile. This work has deliberately not been repaired after the pause request.
- Finish and run the adjacent address-format/offset tests before treating the patch as qualified.
- Recheck release latency under a quiet, controlled CPU workload; the initial 4 KiB paired median ratio was approximately 1.21 with substantial uncertainty.
- Neither candidate patch has been submitted to, reviewed by, or accepted by UCX upstream.

## Files

- `src/clinic.c`: two-process UCP payload verification, bounded request completion/cancellation, resource traces.
- `patches/`: two independent candidate patches; wire field offsets and sizes remain unchanged by the first.
- `scripts/build.py`: isolated builds for alignment, ASan/LSan and release configurations.
- `scripts/validate.py`: subprocess supervisor, raw logs and scoped acceptance results.
- `scripts/benchmark.py`: alternating release comparisons with CPU affinity and tracing disabled.
- `evidence/local/`: complete local logs, retained but excluded from Git by default.

Reference: [upstream issue #11806](https://github.com/openucx/ucx/issues/11806). The issue author's original findings and contribution intent are credited; these local experiments are independent validation.
