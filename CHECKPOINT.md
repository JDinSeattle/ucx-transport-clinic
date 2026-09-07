# Paused checkpoint

Paused at the user's explicit instruction to skip the current project and retain the work. No existing code, patch or raw experiment directory was deleted.

## Verified local records

- `evidence/local/stock-reproduction/`: original stock-example alignment counterexample.
- `evidence/local/alignment-regression/summary.json`: 14 scoped integration cases, all passing.
- `evidence/local/asan-regression/`: first ASan attempt; captured the upstream example's 77-byte leak, so the suite correctly failed.
- `evidence/local/asan-validated/summary.json`: six cases passed after the separate example-lifetime candidate fix.
- `evidence/local/release-comparison/`: seven repeats per size and version, 200 timed ping-pong iterations per run. Large variance remains a limitation.
- `evidence/local/builds/`: exact configure commands, installed prefixes and build logs. The latest `patched-align.log` and `patched-release.log` end with the internal-unit header error documented in README.

The integration results precede the new internal-unit target. They must not be used to imply that the current full build passes. The project is preserved as an unfinished checkpoint.

## Ownership contract implemented in the reproducer

1. A UCP operation may return immediate success, an error pointer, or an owned request.
2. A pending request and its payload buffer remain live until completion or completed cancellation.
3. The supervisor imposes a deadline; an unresolved cancellation causes failure instead of freeing a live request.
4. Controlled peer termination is a local test. Its exit code is expected, while the surviving process must cancel its receive and release resources.
5. Counters expose allocation/completion/free mismatches. The workload has one outstanding data operation per rank at a time.

## Security-trigger context

At the time this UCX investigation was paused, no visible tool response provided the reason for an earlier security-related UI label. The observed runtime permission barriers concerned restricted network/GPU visibility. A later attempt to create a public project repository was explicitly rejected because public visibility and public release of the evidence had not been authorized; private repository preservation was used instead.

Alignment sanitizer findings, memory-lifetime patches, and controlled peer termination are security-adjacent diagnostic operations and could explain a security-related UI label. That is an inference, not a confirmed trigger. The experiments used local UCX/TCP processes and the already-public upstream report; they did not target external services.
