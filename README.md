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

shim Guard scans prompts through native client hooks before they reach the
model. Safe prompts continue silently; detected values are replaced with typed
placeholders such as `<EMAIL_1>`.

> [!WARNING]
> shim Guard is alpha software and a best-effort guard, not a data-loss
> prevention boundary. Read the [privacy limitations](docs/privacy.md) before
> using it with sensitive data.

## Supported clients

| Client | Behavior when a value is detected |
| --- | --- |
| [Codex CLI](https://github.com/openai/codex) | Blocks submission and creates a private redacted file to resubmit |
| [Claude Code](https://github.com/anthropics/claude-code) | Blocks submission and creates a private redacted file to resubmit |
| [GitHub Copilot CLI](https://github.com/github/copilot-cli) | Replaces the model-facing prompt with the redacted text |

shim Guard detects email addresses, phone numbers, credit cards, IBANs, IP and
MAC addresses, US SSNs, Turkish national and tax IDs, secrets, and database
URIs. Detection runs locally without an account, API key, network request,
daemon, telemetry, or prompt history.

## Install

shim Guard supports CPython 3.13 on macOS and Linux. Choose one package
manager:

```console
uv tool install shim-guard
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

## Configure detection

All supported entity types are enabled by default. View or change the local
policy with:

```console
shim config
shim config --only EMAIL --only SECRET
shim config --disable IP_ADDRESS --disable MAC_ADDRESS
shim config --reset
```

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
