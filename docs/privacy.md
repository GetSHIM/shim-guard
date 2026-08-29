# Privacy and trust boundary

## What this does and does not prevent

**With the default configuration, shim Guard does not prevent a secret typed
into a prompt from reaching the model.** It detects the value, reports what it
found, and lets the prompt through. This is a consequence of the hook APIs:
no supported client offers a field for rewriting a submitted prompt, so the
only available alternative is refusing the sentence the user just typed. That
is the most disruptive thing this product can do, and it is opt-in
(`user-prompt = "enforce"`).

What the default configuration *does* prevent is local data entering the
model's context through tool results — a file read, a grep, command output, an
MCP response. Those are masked in place before the model sees them, which is
where most leakage actually happens.

Three things follow, and all three are limits rather than features:

- Masking an outbound tool argument is **egress control**, not model
  protection. The model produced that argument, so it has already seen the
  value; masking stops it leaving the machine.
- `Bash` commands and `Write`/`Edit` content are **never rewritten**. Editing a
  command changes what runs, and editing a write payload puts a placeholder
  into a real file. Both are detected and can be warned about or denied.
- Files referenced with `@` bypass hooks entirely, so nothing here sees them.

## Data flow

```text
client prompt -> trusted SHIM hook -> in-memory offline detector
             <- empty success | Copilot model-facing typed rewrite
                              | native stop response with categories and file path
                                                        |
                                                        -> 0600 typed redaction
```

The hook reads the submitted-prompt fields needed for the native contract. It
does not send prompt data to SHIM, keep history, or create a replacement map.
Safe input produces exactly empty stdout and stderr. For a supported Copilot
finding, the hook returns the typed redaction as the model-facing replacement
and writes no file. For Codex and Claude Code, it writes one typed redaction to
a `0600` file in the operating system's temporary directory and returns a
tested native block containing its absolute path in a ready-to-copy instruction
to use the file contents as the prompt. A handled hook error returns the
client's generic fail-closed response and leaves no suggestion file. The raw
prompt and detected raw values are not written by SHIM.

The temporary redaction remains until the user deletes it or the operating
system cleans temporary storage. It can still contain sensitive content the
detector missed, so users must review it before resubmission and delete it when
finished.

Users may enable or disable public entity types with `shim config`. The default
preset enables all supported types. The local settings file contains entity
names only. An invalid or unsafe settings file causes the hook to return its
generic fail-closed response rather than silently ignoring the policy.

## Outside SHIM's boundary

The host client receives the raw prompt. Matching hooks can start concurrently,
so SHIM cannot stop another matching hook from receiving it. Clients,
operating-system tools, plugins, and providers can retain logs, transcripts,
telemetry, caches, or history independently of SHIM.

Copilot's `userPromptTransformed` replacement changes what is sent to the model
and stored in session history, but the original prompt can remain visible in
Copilot's timeline.

Some clients require review and trust for non-managed hooks. A hook can be
disabled, untrusted after a change, missing, unable to start, crash, or time
out; those outcomes are client-controlled and may fail open. SHIM does not
promise detection of every value, inspect automatic context or tool output, or
securely erase Python process memory.

Installer checks detect unsafe paths and observed drift, but they are not an
isolation boundary against a malicious process already running as the same OS
user. Such a process has equivalent authority over user-scoped client settings
and can race POSIX pathname operations despite advisory locking.

Review every temporary redaction before resubmission. The detector can miss
sensitive content.
