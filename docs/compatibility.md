# Compatibility and evidence

| Area | Current status |
| --- | --- |
| Python | CPython 3.13 target |
| Operating systems | macOS and Linux target |
| Client | Codex CLI 0.149.0 locally inspected |
| Hook feature | `hooks stable true` observed locally |
| Hook contract | Native fixture coverage in the repository |
| Live interactive client | PENDING_RELEASE_EVIDENCE |
| Authentication routes | PENDING_RELEASE_EVIDENCE |
| Trust review and client timeout behavior | PENDING_RELEASE_EVIDENCE |
| Claude Code / second client | Deferred |
| SHIM Protect | Deferred; separate process and threat model |

The local inspection follows the current [Codex hook docs](https://developers.openai.com/codex/hooks/).
Claude Code has its own independently changing hook contract; its current
[documentation](https://code.claude.com/docs/en/hooks) is not a compatibility
claim or an implementation target.

## Development evidence

The versioned `guard-v1` corpus contains 30 exact category-set cases. Its
published metrics report 100% synthetic case-category precision and recall;
every one of the 12 implementation categories has at least one positive and
one targeted safe negative. This is deterministic, fixture-bound contract
evidence—not a real-world statistical guarantee.

The current development measurement came from the final installed wheel: 20
safe and 20 blocking fresh-process invocations, alternated without a warm-up.
It ran on
Darwin 25.5.0 arm64, macOS 26.5.2, CPython 3.13.9:

| Fixture | p50 | p95 | Maximum |
| --- | ---: | ---: | ---: |
| Safe prompt | 2,410 ms | 4,262 ms | 6,493 ms |
| Email block | 2,460 ms | 3,968 ms | 4,244 ms |

This is development evidence, not tag-generated release evidence. Detector
analysis has a 20-second deadline; the 25-second outer hook deadline covers
stdin, bootstrap, and evaluation; the Codex configuration timeout is 30
seconds. Loaded hosts and cold filesystem caches can be slower; a client
timeout remains fail-open behavior outside SHIM's control.

## Release evidence gate

The tag workflow refuses publication while a release-evidence marker remains.
Before removing those markers, record a real interactive Codex run for the
release tag, its authentication route, trust activation, and observed
timeout/fail-open behavior. CI fixtures do not establish those facts.
Repository setup must also protect `v*` tags and require reviewers for the
`release` and `pypi` environments; a workflow file cannot enforce those
GitHub-side controls.

The tag workflow generates `benchmark-hook.json` from 20 safe and 20 blocking
fresh-process invocations, alternated one pair at a time. It rejects safe or
blocking p95 above 5,000 ms and publishes the platform, Python version, sample
counts, p50, p95, and maximum—never prompt text. A separate fresh runner builds
from the tagged commit archive and publishes only when its wheel and source
distribution byte-match the tested pair. CI runs the same latency check with
one safe and one blocking invocation. Release artifacts also include this file,
`guard-v1.json`, `guard-v1-metrics.json`, `requirements.lock`, package hashes,
an SBOM, and attestations. The requirements lock reproduces the tested runtime
dependencies; ordinary `pip install shim-guard` may resolve newer compatible
transitives.

The release record must contain:

| Evidence | Release value |
| --- | --- |
| Supported Codex version and platform | PENDING_RELEASE_EVIDENCE |
| Authentication route(s) tested | PENDING_RELEASE_EVIDENCE |
| Trusted-hook activation and fail-open observations | PENDING_RELEASE_EVIDENCE |
| Synthetic corpus and quality metrics | `guard-v1` and `guard-v1-metrics.json` |
| Fresh-process latency | `benchmark-hook.json`, generated for the tag |
