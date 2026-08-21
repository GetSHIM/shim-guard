# Security policy

## Supported versions

Only the latest released `shim-guard` version receives security fixes. This
repository is pre-release until a version is published.

## Reporting a vulnerability

Please report suspected vulnerabilities through a private GitHub Security
Advisory for this repository. Do not include exploit details in a public issue.

Include the affected version, environment, reproduction steps, impact, and any
relevant redacted logs. Please avoid sending real secrets or personal data. We
will acknowledge the report, assess it, and coordinate disclosure through the
advisory.

## Scope notes

The hook is a local best-effort guard, not an enforcement boundary. A report
involving prompt leakage, unsafe hook output, persistence beyond the documented
private redaction file in OS temporary storage, network access, installer
ownership failures, or a fail-open path is in scope.
