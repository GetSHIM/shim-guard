# Architecture and design change plan

- Status: proposed implementation plan
- Target: `shim-cli` at `b817369`
- Released baseline: `v0.1.3` at
  `c7625b3e418ded3c7234c7255d8232c75b3f6417`

This document defines the changes required before the next release. It is an
execution plan, not a description of the current implementation;
`docs/architecture.md` remains the current architecture contract until the work
below is complete.

The goal is not a larger architecture or a rewrite for its own sake. The goal is
the smallest structure in which an agent or maintainer can find an entry point,
decision, state owner, external effect, and relevant check without tracing the
whole repository. "Perfect" means that the executable invariants in this plan
hold. It does not mean introducing every available pattern.

## Decision

Keep shim Guard as one domain-sliced modular monolith and one Python
distribution. There is no demonstrated need for Polylith, services, a plugin
framework, dependency injection, or an architecture-specific library.

Make a targeted refactor with this order of precedence:

1. Correct trust-boundary defects before moving code.
2. Give each policy, protocol, and persisted record one obvious owner.
3. Keep dependencies acyclic and enforce the valuable boundaries in the
   existing import-hygiene contract.
4. Keep I/O and failure behavior visible at `hook.py`, CLI, settings-file,
   session, and proxy boundaries.
5. Preserve proven behavior unless a change is explicitly listed here.
6. Stop when the acceptance criteria pass. Do not continue refactoring because
   another decomposition is possible.

## Evidence for changing the current branch

The `shim-cli` branch is a large product change even though the repository is
still small: ten commits changed 179 files with 28,606 additions and 1,563
deletions. Generated suffix and corpus data account for much of that size, but
the branch also added tool-event policy, context diet, session persistence, and
the `shim watch` proxy.

The current source import graph is acyclic and the existing tests mirror the
packages well. Those are strengths to preserve. The following problems are the
evidence for structural work:

| Evidence | Consequence |
| --- | --- |
| `hook.py` is 541 lines and changed in six of the ten branch commits. | The process boundary also owns session persistence and native response details, making unrelated changes converge on one file. |
| `events/pipeline.py` changed in five of the ten commits. | Its shared policy path is important, but client encoding and persisted-record ownership should not accumulate there. |
| `guard/analyze.py`, `guard/evaluate.py`, and `guard/models.py` import `config.py` for the detector's entity catalog. | The detector depends upward on user configuration instead of owning its public vocabulary. |
| `Policy`, default modes, and mode validation live in `config.py`, while directions, the same mode vocabulary, actions, and `Decision` live in `events/policy.py`. | One policy concept has two owners and duplicate constants. |
| `events/record.py` defines the persisted schema while `session/summary.py` imports it. | The state owner depends on an event implementation detail. |
| Prompt codecs live below `clients/<client>/`, while tool-event codecs live in shared `events/adapters.py`. | A client's native protocol cannot be understood in one place. |
| The registry contains unverified Codex, Copilot, and Claude failure adapters that are not installed. | This is future scaffolding with maintenance and test cost but no supported behavior. |
| A per-tool entity override replaces the evaluator before `_target()` scrubs a target path or URL. A target containing an entity outside that override is consequently persisted verbatim. | User policy can accidentally weaken a non-configurable storage-safety boundary. |
| Raw tool, unsupported-event, and provider-model labels can reach records or terminal output. Control characters survive JSON encoding. | Untrusted display metadata is not normalized at ingress. |
| Development has many correct commands but no single verification entry point. The exact uv CLI patch pin also rejects newer `0.12.x` clients. | A fresh agent must reconstruct the check sequence and may be unable to start it. |
| Source comments refer to unavailable `PRD-01` through `PRD-10` documents. | Important rationale is not repository-local or directly discoverable. |
| The README says there is no network call, while `shim watch` is a network forwarding proxy; compatibility documentation calls the detector standard-library-only while it imports `phonenumbers`. | Published constraints do not match executable behavior. |

## Non-negotiable behavioral invariants

All structural commits must preserve these rules unless the same commit updates
this plan, the public documentation, and an executable contract for an approved
behavior change.

### Hook and privacy

- The hook stays synchronous, local, bounded, and independent of `watch/`.
- Safe hook input produces exactly empty stdout and stderr.
- A handled prompt failure fails closed with a generic native response. A tool
  event that cannot be inspected passes through unchanged and reports the
  failure where the client supports reporting; it must not deny already-created
  work.
- No prompt, finding value, replacement value, shell command, request body, or
  response body is logged or persisted.
- Stored targets are always scrubbed with the full detector entity set. User
  entity policy may narrow payload detection, never storage sanitization.
- Untrusted client, event, tool, target, and model labels are bounded and safe
  for their destination before they are rendered or persisted.
- Prompt redaction suggestions remain `0600`, live only in OS temporary storage,
  and are removed on serialization failure or by the existing age-bound sweep.

### Settings files

- Settings reads and writes retain the current no-target-symlink,
  ownership/permissions, size, identity, concurrency, atomic-publication, and
  idempotency checks.
- Install changes only SHIM's exact fragment and preserves unrelated settings
  and hooks. Revert removes only that fragment and never deletes the document.
- Malformed, ambiguous, unsafe, or concurrently changed files require manual
  action. Development and tests use temporary paths, never real user settings.
- Existing valid `config.toml` files remain valid. No migration alias or dual
  parser is added unless an actual format change is approved.

### Detector and evidence

- The detector remains deterministic, offline, bounded by input size, and
  independently packaged.
- Detection never depends on CLI, configuration, client, event, session, or
  watch implementation modules.
- `guard-v2.json`, `guard-tools-v1.json`, and `parity-v1.json` remain executable
  contracts. `parity-v1.json` is not regenerated to make a refactor pass.
- Credential detection is not relaxed. Deliberate behavior changes require an
  explicit corpus change and rationale.
- `recognizers.py` remains one cohesive recognizer catalog. Its line count alone
  is not evidence for splitting every entity into a module.

### Session and watch

- Session recording remains best-effort: a spool or ledger failure cannot
  disable masking or blocking.
- The session spool and ledger contain only the bounded record schema, with a
  hashed session filename and existing directory/file permissions.
- `Stop` renders a summary only when needed; `SessionEnd` cleans up and does not
  depend on rendered output.
- `shim watch` binds loopback, forwards the client's bytes unchanged, originates
  no provider request, does not retry a `POST`, and persists no body.
- Proxy measurement stays beside the forwarding path. Exact provider usage is
  never presented as the same thing as inferred section attribution.
- Release integrity checks remain intentionally redundant where independent
  workflows protect different trust boundaries.

## Target ownership

The end state uses ordinary Python packages and functions. Public contracts are
module-level values, dataclasses, and callables; there is no container or
framework-managed wiring.

| Owner | Responsibility | Must not own |
| --- | --- | --- |
| `guard/` | Entity catalog and normalization, detection, findings, spans, redaction, recognizers. | Configuration files, client protocol, persistence, CLI, or network behavior. |
| `policy.py` | Directions, modes, actions, defaults, runtime `Policy`, `Decision`, classification, and action selection. | TOML parsing, native response JSON, or I/O. |
| `config.py` | Config path, TOML parse/render, validation at file ingress, and loading a `Policy`. | Detector vocabulary, duplicate policy constants, or generic filesystem primitives. |
| `events/` | Deterministic shared tool-payload traversal, diet, injection markers, and the tool-event decision pipeline. | Client-native serialization, user settings, or persisted-state ownership. |
| `clients/<client>/` | Native prompt and verified tool-event codecs, capability facts, settings fragments, and coexistence rules for one client. | Shared detection or session logic. |
| `session/` | The `Record` schema, timestamping, best-effort `remember()`, spool, ledger, cleanup, and summary. | Tool-payload inspection or client-native encoding. |
| `settings_files/` | Guarded inspect, pure plan, parent creation, revalidation, and atomic apply for user-scoped settings. | Client-specific merge semantics or policy parsing. |
| `watch/` | Proxy forwarding, wire measurement, and watch report values. | Hook imports, hook settings, or session records. |
| `hook.py` | Deadline, bounded stdin, envelope dispatch, visible failure semantics, temporary prompt-file lifecycle, and stdout. | Persisted schema definitions, ledger/spool mechanics, or native tool-event codecs. |
| `cli/` | Command composition and human/JSON presentation. | Domain rules duplicated from owners above. |

`installation/` becomes `settings_files/` because it is already the shared
guarded settings-file boundary used by policy configuration and client
installation. This is an ownership rename, not a rewrite. Move the existing
implementation and tests; do not weaken or redesign its filesystem checks.

The intended dependency direction is:

```text
cli  ------> config, clients, session, settings_files, watch, guard
hook ------> config, clients, events, session, guard
config ----> policy, guard/entities, settings_files, events/diet
clients ---> policy, events, session, settings_files
events ----> policy, guard, session/record

guard, policy, session, settings_files, watch
    do not import hook, cli, config, or one another except where listed above.
hook does not import watch.
session does not import events.
guard does not import config.
```

Composition remains explicit in `hook.py` and the CLI. If the final import graph
needs a reverse edge not listed above, first try passing an existing callable or
value at the current composition point. Add a port only for a real client or
external boundary with more than one live implementation.

## Required changes

### Phase 0 — close trust-boundary gaps

Complete this phase before moving modules.

1. In `events/pipeline.py`, compute and scrub the record target with the original
   full evaluator before applying a per-tool entity scope to payload analysis.
   Do not add a sanitizer framework; the existing evaluator already supplies the
   required behavior.
2. Add a regression in `tests/events/test_pipeline.py` in which policy scans only
   `SECRET` while the target contains an email address. Assert that neither the
   `Record` nor its dictionary representation contains the address. Cover the
   URL/path form that `_target()` actually accepts.
3. Normalize untrusted display metadata at its ingress owner:
   - tool-event records use a printable, bounded tool label;
   - an unsupported event is recorded as a fixed safe label, not raw input;
   - watch reports use a printable, bounded model label.
   Keep the raw tool name only as long as policy classification needs it. Invalid
   display labels fall back to a fixed value rather than being echoed or causing
   payload inspection to be skipped.
4. Add focused control-character and over-bound label tests beside the event and
   watch owners. Assert on stored and rendered output, not only helper return
   values.
5. Correct the network and third-party dependency claims in `README.md`,
   `docs/privacy.md`, and `docs/compatibility.md`. The accurate statement is that
   the hook adds no network destination, account, API key, or telemetry;
   `shim watch` forwards only to the provider the client was already configured
   to use. The detector is first-party and offline, with `phonenumbers` as the
   one third-party hook-path dependency.
6. Fix the module-level no-local-leakage skip so it is legal when the test is run
   outside a Git checkout. Do not weaken the scan or add a branch-only bypass.

Phase 0 acceptance:

- The target-leak regression fails on `b817369` and passes after the fix.
- Raw control characters and over-bound labels appear in neither session JSONL
  nor terminal/report output.
- Payload scanning behavior and policy matching are unchanged for valid labels.
- The relevant event, session, watch, and contract tests pass.

### Phase 1 — establish one owner per contract and state

Make each item a separate behavior-preserving commit where practical.

1. Add `guard/entities.py` and move `ENTITY_TYPES`, `DEFAULT_ENTITIES`, and
   `normalize_entities()` there. Export the public catalog from `guard`.
   `config.py` and CLI consumers import that contract; no file below `guard/`
   imports `config.py`.
2. Replace `config.py` plus `events/policy.py` policy ownership with one top-level
   `policy.py`. It owns the only definitions of directions, modes, actions,
   defaults, `Policy`, `Decision`, `direction_for()`, and `decide()`.
   `config.py` only parses/renders TOML and loads that runtime value.
3. Move `Record`, `NOT_INSPECTED`, timestamp creation, and best-effort recording
   into `session/record.py`. Expose one `session.remember()` operation that owns
   spool and optional ledger writes. It must catch storage failures at this
   effect boundary while keeping record construction and validation explicit.
   `session/summary.py` must not import `events`.
4. Rename `installation/` to `settings_files/` and move its mirrored tests in the
   same commit. Update internal imports directly; do not retain a compatibility
   package or alias because no such compatibility layer is requested.

Phase 1 acceptance:

- `guard/` has no import of `config`, `events`, `session`, `clients`, `watch`,
  `hook`, or `cli`.
- There is one definition of each policy constant and one runtime `Policy`.
- `session/` has no import of `events`, and every persisted record is created or
  validated by the session owner.
- Every settings-file safety and install/revert test passes unchanged in intent.
- No new runtime dependency, architecture framework, generic `common`,
  `helpers`, `misc`, or `utils` module is introduced.

### Phase 2 — make client protocols local and the runner narrow

1. Delete unverified Codex, Copilot, and Claude failure tool-event adapters and
   their scaffold-only tests. A future adapter starts with a live protocol probe,
   a captured synthetic fixture, and a documented mutation/report channel; it is
   not reintroduced by changing a `verified` flag.
2. Move verified Claude tool-event response encoders and its installed-event
   list to `clients/claude/tool_events.py`. Claude settings derive hook groups
   from that local verified list. Codex and Copilot keep their proven prompt
   codecs and settings behavior but gain no empty tool-event modules.
3. Keep the shared payload walk and policy pipeline in `events/`. Pass the chosen
   client adapter into the pipeline at the explicit hook composition point;
   `events/` must not import client modules. Use the existing frozen dataclass and
   callables as the contract rather than adding an inheritance hierarchy.
4. Reduce `hook.py` by moving native error/tool response shapes to the relevant
   client codec and session schema/timestamp/spool/ledger/summary mechanics to
   `session/`. Keep the deadline, capped read, envelope dispatch, temporary
   suggestion lifecycle, policy loading, fail-open/fail-closed selection, and
   final stdout write visible in `hook.py`.
5. Do not split `events/pipeline.py` or `guard/recognizers.py` merely to meet a
   line-count target. Split only if the ownership moves above leave a second
   independently changing responsibility with its own callers and tests.

Phase 2 acceptance:

- One client can be understood by reading its directory plus the shared event
  pipeline; its native JSON shapes are not in a cross-client adapter bucket.
- Only events verified against a running client are represented as supported
  tool adapters or installed hooks.
- `events/` has no import of `clients/`, and client settings do not import a
  shared future-adapter registry.
- `hook.py` contains no record dataclass, timestamp formatter, spool/ledger write,
  or client-native tool-event JSON document.
- Safe stdout/stderr, prompt failure, tool failure, session summary, installation,
  and native fixture contracts all pass.

### Phase 3 — make constraints executable and release-ready

1. Add one standard-library `scripts/check.py` entry point that invokes the
   existing locked sync, Ruff lint/format, ty, pytest, zipapp build, distribution
   build, and whitespace checks. It should be direct `subprocess.run(...,
   check=True)` sequencing, not a task framework. CI remains responsible for the
   supported Python-version matrix.
2. Change the uv CLI requirement from exact `==0.12.5` to the compatible
   `>=0.12.5,<0.13` range. Keep `uv-build==0.12.5` exact in both the development
   and build-system requirements so artifact construction remains reproducible.
3. Extend `tests/contracts/test_import_hygiene.py` rather than creating a second
   architecture framework. Enforce the dependency prohibitions in the target
   graph and keep the existing hook/watch and third-party import checks.
4. Replace opaque `PRD-01` through `PRD-10` references with self-contained
   rationale or a repository-local decision link. Do not copy historical prose
   when a fixture, corpus, or test already expresses the rule.
5. Update `docs/architecture.md`, `docs/privacy.md`, `docs/compatibility.md`,
   `README.md`, and `AGENTS.md` to describe the completed implementation and the
   single check command. Remove claims that only described an intermediate
   branch state.
6. Release the result as `0.2.0`, not another `0.1.3` artifact: the branch adds
   public commands, policy behavior, session files, installed hooks, and an
   opt-in proxy. List those public and persisted-surface changes explicitly in
   release notes; do not add legacy command aliases or dual APIs.
7. Run the existing tag evidence gate without weakening its independent build,
   hash, SBOM, attestation, corpus, benchmark, client, authentication, and
   protected-environment requirements.

Phase 3 acceptance:

- `python scripts/check.py` is the documented local verification command and
  succeeds from a fresh locked environment.
- CI passes on every supported Python version, including 3.9 and 3.13.
- The import contract fails on a forbidden edge and passes on the target graph.
- Repository source and current documentation contain no unexplained external
  PRD references or false network/dependency claims.
- The package, plugin archive, lock, version, documentation, and release
  evidence all describe the same `0.2.0` behavior.

## Compatibility policy

This refactor is behavior-preserving for valid existing configurations, native
hook envelopes, session summaries, the detector API, corpus output, install and
revert semantics, and watch forwarding. Internal Python module paths are not a
promised public API; move them directly without compatibility packages.

Any change to these surfaces is not an incidental refactor and requires a
separate explicit decision in the implementing commit:

- CLI command, option, exit status, stdout, stderr, or JSON shape;
- native client hook input/output shape or installed settings fragment;
- `config.toml` keys, defaults, or precedence;
- session or ledger record schema and retention;
- placeholder, finding, span, score, or entity ordering;
- watch forwarding, header, TLS, streaming, or usage semantics;
- package/plugin entry points and supported Python/client versions.

Do not add migration shims pre-emptively. If a real released surface must break,
document the break, add the smallest migration check that protects user data,
and version it accordingly.

## Commit and verification discipline

- Use small commits with one behavioral or ownership claim each. Do not perform
  the work as a big-bang directory rewrite.
- For a bug, inspect every caller and fix the shared root cause once. Keep one
  focused regression that fails before the fix.
- For a move, preserve behavior first; simplify only after the moved owner and
  its tests are stable.
- Prefer deletion, direct functions, frozen data values, the standard library,
  and installed dependencies. Add no interface with one implementation, factory,
  speculative fallback, retry, custom exception hierarchy, global mutable
  registry, metaprogramming, or async path.
- Run the narrow mirrored tests after each commit and the full check entry point
  after each phase. Inspect `git diff --check` and the actual changed paths before
  moving on.
- Preserve unrelated worktree changes. Do not regenerate generated evidence or
  reformat untouched files to make a structural diff easier.

Suggested commit sequence:

1. Fix target scrubbing and label ingress with regressions.
2. Correct published claims and the contract-test skip.
3. Move the detector entity contract.
4. Consolidate policy ownership.
5. Move session record ownership and recording effects.
6. Rename the guarded settings-file owner.
7. Delete unverified adapters and localize the verified Claude protocol.
8. Narrow `hook.py` without changing behavior.
9. Add the single check command and extend import contracts.
10. Remove opaque references, update normative documentation, and prepare the
    `0.2.0` evidence release.

## Stop conditions and non-goals

Stop the refactor when the definition of done below passes. Specifically, do
not:

- split into services, Polylith components, or multiple distributions;
- introduce a provider-neutral plugin system or dynamic client discovery;
- create `common`, `misc`, `helpers`, or `utils` dumping grounds;
- add ports for hypothetical storage, proxy, detector, or configuration
  replacements;
- rewrite the guarded file publisher, watch proxy, detector, recognizer catalog,
  corpus, or release integrity system without a reproduced defect;
- split every recognizer, transform, event, or CLI command into its own module;
- optimize hook or proxy code without a benchmark showing a regression;
- add compatibility aliases for internal moves;
- continue rearranging code after ownership, dependency, trust-boundary, and
  verification criteria are met.

## Definition of done

The work is complete only when all of the following are true:

- [ ] The target-scrubbing and untrusted-label regressions pass.
- [ ] All privacy, prompt, tool-event, settings-file, session, detector, corpus,
      proxy, plugin, and release invariants above are green.
- [ ] Every behavior, persisted record, external effect, and native protocol has
      the owner listed in the target table.
- [ ] The import graph is acyclic and the valuable prohibited edges are enforced
      by the existing import-hygiene contract.
- [ ] `hook.py` is an explicit process/effect boundary rather than the owner of
      session storage or client-native tool serialization.
- [ ] One documented local command runs the full verification sequence, and CI
      covers the supported interpreter matrix.
- [ ] Public, privacy, compatibility, architecture, agent, package, plugin, and
      release documentation agree with executable behavior.
- [ ] Public or persisted changes are called out for `0.2.0`; no unrequested
      compatibility layer exists.
- [ ] The final diff contains no unrelated refactor, generated-evidence rewrite,
      local-machine data, or formatting churn.

After completion, make `docs/architecture.md` normative for the final module
map and trim this file to the non-obvious decisions and migration record. Do not
leave the checklist as a second, drifting architecture specification.
