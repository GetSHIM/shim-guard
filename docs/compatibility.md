# Compatibility and evidence

| Area | Current status |
| --- | --- |
| Python | CPython 3.13 target |
| Operating systems | macOS and Linux target |
| Clients | Codex CLI 0.149.0, Claude Code 2.1.210, and GitHub Copilot CLI 1.0.80 locally inspected |
| Hook feature | Codex `hooks stable true`; Claude Code generated settings accepted by `claude doctor`; Copilot `userPromptTransformed` documented and locally present |
| Hook contract | Native fixture coverage for all three clients in the repository |
| Live interactive client | All three clients exercised on macOS 26.5.2 arm64 |
| Authentication routes | Codex ChatGPT sign-in, Claude Code first-party sign-in, and GitHub Copilot OAuth |
| Trust review and client timeout behavior | Hooks reviewed and activated; safe and finding prompts exercised; forced timeout/error behavior observed to fail open |
| SHIM Protect | Deferred; separate process and threat model |

The local inspection follows the current [Codex hook docs](https://developers.openai.com/codex/hooks/).
The Claude Code adapter follows its current
[hooks](https://code.claude.com/docs/en/hooks) and
[settings](https://code.claude.com/docs/en/settings) references. It uses the
user-scoped `settings.json`, shell-free command arguments, empty safe output,
and the native structured block response with original-prompt display
suppressed.

The GitHub Copilot CLI adapter follows GitHub's current
[hooks reference](https://docs.github.com/en/copilot/reference/hooks-reference).
Command hooks cannot change `userPromptSubmitted`, so SHIM uses
`userPromptTransformed` to replace the model-facing content with its typed
redaction. The original prompt can remain visible in Copilot's timeline.

## Development evidence

The versioned `guard-v1` corpus contains 27 exact category-set cases. Its
published metrics report 100% synthetic case-category precision and recall;
every one of the 11 implementation categories has at least one positive and
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
stdin, bootstrap, and evaluation; all client configurations use a 30-second
timeout. Loaded hosts and cold filesystem caches can be slower; a client
timeout remains fail-open behavior outside SHIM's control.

## Release evidence gate

The tag workflow refuses publication while a release-evidence marker remains.
Before removing those markers, record real interactive Codex, Claude Code, and
GitHub Copilot CLI runs for the release tag, their authentication routes, hook
activation, and observed timeout/fail-open behavior. CI fixtures do not
establish those facts.
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
| Supported client versions and platforms | Codex CLI 0.149.1, Claude Code 2.1.210, and GitHub Copilot CLI 1.0.80 on macOS 26.5.2 arm64 with CPython 3.13.9 |
| Authentication route(s) tested | Codex ChatGPT sign-in, Claude Code first-party sign-in, and GitHub Copilot OAuth |
| Trusted-hook activation and fail-open observations | Native hooks reviewed and activated; safe prompts continued, findings blocked or rewrote as designed, and forced timeout/error behavior failed open at the client boundary |
| Synthetic corpus and quality metrics | `guard-v1` and `guard-v1-metrics.json` |
| Fresh-process latency | `benchmark-hook.json`, generated for the tag |
