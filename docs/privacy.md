# Privacy and trust boundary

## Data flow

```text
Codex prompt -> trusted SHIM command hook -> in-memory offline detector
           <- empty success | native stop response with categories and file path
                                      |
                                      -> 0600 typed redaction in OS temporary storage
```

The hook reads the submitted-prompt fields needed for the native contract. It
does not send prompt data to SHIM, keep history, or create a replacement map.
Safe input produces exactly empty stdout and stderr. For a supported finding,
the hook writes one typed redaction to a `0600` file in the operating system's
temporary directory and returns a tested native block containing its absolute
path in a ready-to-copy instruction to use the file contents as the prompt. A
handled hook error returns the generic block and leaves no suggestion file. The
raw prompt and detected raw values are not written by SHIM.

The temporary redaction remains until the user deletes it or the operating
system cleans temporary storage. It can still contain sensitive content the
detector missed, so users must review it before resubmission and delete it when
finished.

Users may enable or disable public entity types with `shim config`. The default
preset enables all supported types. The local settings file contains entity
names only. An invalid or unsafe settings file causes the hook to return its
generic block rather than silently ignoring the policy.

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

Review every temporary redaction before resubmission. The detector can miss
sensitive content.
