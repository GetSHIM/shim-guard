# Privacy and trust boundary

## Data flow

```text
Codex prompt -> trusted SHIM command hook -> in-memory offline detector
           <- empty success | native stop response with categories and typed suggestion
```

The hook reads the submitted-prompt fields needed for the native contract. It
does not send prompt data to SHIM, write it to disk, keep history, or create a
replacement map. Safe input produces exactly empty stdout and stderr. A
supported finding or handled hook error returns the tested native blocking
response without raw detected values.

## Outside SHIM's boundary

Codex receives the raw prompt. Matching hooks can start concurrently, so SHIM
cannot stop another matching hook from receiving it. Codex, operating-system
tools, plugins, and providers can retain logs, transcripts, telemetry, caches,
or history independently of SHIM.

Codex requires review and trust for non-managed command hooks. A hook can be
disabled, untrusted after a change, missing, unable to start, crash, or time
out; those outcomes are client-controlled and may fail open. SHIM does not
promise detection of every value, inspect automatic context or tool output, or
securely erase Python process memory.

Installer checks detect unsafe paths and observed drift, but they are not an
isolation boundary against a malicious process already running as the same OS
user. Such a process has equivalent authority over user-scoped Codex files and
can race POSIX pathname operations despite advisory locking.

Review every suggested redaction before resubmission. The detector can miss
sensitive content.
