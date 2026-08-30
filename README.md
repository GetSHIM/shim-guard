<p align="center">
  <a href="https://getshim.tech">
    <img src="docs/assets/shim-logo.svg" alt="shim" width="280">
  </a>
</p>

<h1 align="center">shim Guard</h1>

<p align="center">
  <strong>Local, offline privacy protection for coding-agent prompts.</strong><br>
  <a href="https://getshim.tech">getshim.tech</a>
</p>

<p align="center">
  <a href="https://pypi.org/project/shim-guard/"><img src="https://img.shields.io/pypi/v/shim-guard.svg?logo=pypi&amp;label=PyPI" alt="PyPI version"></a>
  <a href="https://github.com/GetSHIM/shim-guard/actions/workflows/ci.yml"><img src="https://github.com/GetSHIM/shim-guard/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/shim-guard/"><img src="https://img.shields.io/pypi/pyversions/shim-guard.svg?logo=python&amp;logoColor=white" alt="Python versions"></a>
  <a href="https://github.com/GetSHIM/shim-guard/blob/main/LICENSE"><img src="https://img.shields.io/github/license/GetSHIM/shim-guard.svg" alt="License"></a>
  <a href="https://github.com/GetSHIM/shim-guard/stargazers"><img src="https://img.shields.io/github/stars/GetSHIM/shim-guard.svg?style=flat&amp;logo=github" alt="GitHub stars"></a>
</p>

shim Guard scans prompts and tool traffic through native client hooks. Safe
input continues silently; detected values are reported or replaced with typed
placeholders such as `<EMAIL_1>`, depending on where the data is going.

> [!IMPORTANT]
> **With the default configuration, shim Guard does not prevent a secret you
> type into a prompt from reaching the model. It tells you afterwards.**
> No client offers a field for rewriting a submitted prompt, so the only way to
> stop one is to refuse the sentence you just typed — which is disruptive and
> rare enough that it is not the default. Set `user-prompt = "enforce"` in
> `[mode]` to block instead. Tool results *are* masked before the model sees
> them, and that is where most leakage happens.

> [!WARNING]
> shim Guard is alpha software and a best-effort guard, not a data-loss
> prevention boundary. Read the [privacy limitations](docs/privacy.md) before
> using it with sensitive data.

## Supported clients

| Client | Your typed prompt | Tool input and results |
| --- | --- | --- |
| [Claude Code](https://github.com/anthropics/claude-code) | Reports what it found and lets it through; blocks under `enforce` | **Masked before the model sees them** |
| [Codex CLI](https://github.com/openai/codex) | Reports what it found and lets it through; blocks under `enforce` | Reported only — Codex has no surgical result rewrite |
| [GitHub Copilot CLI](https://github.com/github/copilot-cli) | Replaces the model-facing prompt with the redacted text | Reported only, pending verification |

Tool coverage is verified against a running client, not derived from
documentation. `shim doctor <client>` prints exactly which events are installed
and what SHIM can and cannot change at each one.

shim Guard detects email addresses, phone numbers, credit cards, IBANs, IP and
MAC addresses, US SSNs, Turkish national and tax IDs, secrets, and database
URIs. Detection runs locally without an account, API key, network request,
daemon, telemetry, or prompt history.

## Install

shim Guard supports CPython 3.9 through 3.13 on macOS and Linux, including
the system `python3` on macOS. Choose one package manager:

```console
uv tool install --compile-bytecode shim-guard
# or
pipx install shim-guard
```

Preview and install the hook for your client:

```console
shim install codex --dry-run
shim install codex
shim doctor codex
```

Replace `codex` with `claude` or `copilot` as needed. Run `shim help` for all
commands.

### Marketplace plugins

Codex and Claude Code users can install the repository's marketplace plugin
after installing the CLI:

```console
codex plugin marketplace add GetSHIM/shim-guard
codex plugin add shim-guard@shim-guard
```

```text
/plugin marketplace add GetSHIM/shim-guard
/plugin install shim-guard@shim-guard
```

The marketplace plugin and `shim install` are alternative installation
methods. Do not use both for the same client.

## Use

Once the hook is installed, use your client normally. To inspect text directly,
pipe it through `scan` or `redact`:

```console
printf '%s' 'Contact me at alice@example.com' | shim scan
printf '%s' 'Contact me at alice@example.com' | shim redact
```

Both commands read standard input. Do not pass real prompts as command-line
arguments, where they may be recorded in shell history or process listings.

## See what it did

shim says nothing when it works, so it keeps a short record of its own
decisions and shows you the total at the end of any turn where something
changed:

```text
shim — this session
  masked    3 SECRET  (Read .env, Bash)
            2 DB_URI  (Read docker-compose.yml)
  warned    1 EMAIL  (your prompt)
  overhead  6 ms median, 14 ms p95
```

`shim report` prints the same summary on demand, and `--json` makes it
scriptable. During a session it reads the live record; once the client has
closed it falls back to the retained ledger, if you turned that on.

## Shrink tool results

shim compacts tool results before the model reads them, so the context window
fills more slowly. Only losslessly: JSON keeps every number literal, duplicate
key and string byte-for-byte, and only the whitespace between tokens goes.
Nothing is truncated or summarised, and a result that cannot be shrunk safely
is passed through untouched.

It applies to tool *results* only — never to a tool's arguments, never to
anything written to your disk, and never under `mode = "observe"`.

**It also never touches a result that shows you a file.** `Edit` matches its
`old_string` against what is on disk, not against what the model was shown, so
compacting a pretty-printed file on the way in makes the next edit of that file
miss. Reads, notebooks and edit results are passed through byte-for-byte;
fetched pages, command output and tool results are where the saving comes from.

Turn it off with `shim config --no-diet`, or name individual transforms in the
config file:

```toml
diet = ["json"]   # or false to disable entirely
```

While reading results shim also flags text that is trying to give the model
orders — "ignore all previous instructions", impersonated system messages,
invisible characters. These are **reported and never acted on**: rewriting a
tool result because it reads as imperative would corrupt legitimate content.
They appear in the session summary as `flagged`, naming the file they came
from — which is the only part you can act on:

```
  flagged   1 INSTRUCTION_OVERRIDE  (Read release-notes.md)
            1 HIDDEN_TEXT  (Read release-notes.md)
```

The record holds entity names, counts and the file or URL involved — never the
value that was found, and never a shell command. It lives in a private file for
as long as the client session does and is deleted when the session ends.
`shim config --ledger` opts in to keeping it for 30 days instead;
`shim ledger purge` deletes it. Nothing is ever transmitted. See
[Privacy](docs/privacy.md#what-is-recorded).

## Configure detection

All supported entity types are enabled by default. View or change the local
policy with:

```console
shim config
shim config --only EMAIL --only SECRET
shim config --disable IP_ADDRESS --disable MAC_ADDRESS
shim config --reset
shim config --ledger        # keep the session record for 30 days
shim config --no-diet       # stop shrinking tool results
```

`shim config` with no arguments prints the entity table plus the current
ledger and diet state, so what shim keeps and what it rewrites is answerable
without opening the file.

Detection can also be narrowed for one tool at a time, which the CLI has no
flag for — scan commands for secrets without scanning every file read for
phone numbers:

```toml
[entities]
Bash = ["SECRET", "DB_URI"]
Read = ["SECRET"]
```

Every key in the file is optional. A file holding only `[mode]` or only
`[entities]` is valid and everything else keeps its shipped default.

Changes are previewed before they are saved. The CLI, installed hook, `scan`,
and `redact` all use the same policy.

## Privacy limitations

- The host client receives the raw prompt before its hook runs, and other hooks
  may receive it concurrently.
- Detection is best-effort and may miss sensitive values.
- A disabled, untrusted, crashed, or timed-out hook may fail open according to
  client behavior.
- Clients, providers, and other tools may retain data independently of shim.
- Redacted temporary files may still contain missed sensitive content. Review
  them before resubmission and delete them when finished.

See [Privacy](docs/privacy.md) for the full trust boundary and
[Compatibility](docs/compatibility.md) for tested versions and evidence.

## Uninstall

Remove shim Guard's hook before uninstalling the package:

```console
shim revert codex
```

Replace `codex` with the client you installed.

## Project documentation

- [Architecture](docs/architecture.md)
- [Compatibility](docs/compatibility.md)
- [Privacy](docs/privacy.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

## License

Apache-2.0. See [LICENSE](LICENSE).
