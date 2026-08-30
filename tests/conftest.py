"""Keep every test out of the developer's real files.

`shim` resolves three roots from the environment: the config file, the session
spool, and the ledger. Nothing pointed the last one somewhere safe, so any test
that exercised a policy with the ledger enabled appended to the developer's own
`~/.local/state/shim-guard/`. A privacy tool writing records outside its sandbox
during its own test run is the wrong way round, and it made results depend on
whatever happened to be on the machine.

Autouse and unconditional: a test that wants one of these somewhere specific
still sets it, and its `setenv` wins over what this put there first.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_roots(monkeypatch, tmp_path):
    root = tmp_path / "shim-roots"
    # `SHIM_GUARD_STATE_DIR` outranks `XDG_STATE_HOME`, so a developer who has
    # it exported would otherwise escape this fixture entirely.
    monkeypatch.delenv("SHIM_GUARD_STATE_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(root / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root / "config"))
    monkeypatch.setenv("SHIM_GUARD_SESSION_DIR", str(root / "session"))
    # `config_path` prefers this on macOS, where it is not otherwise set.
    monkeypatch.delenv("SHIM_GUARD_CONFIG", raising=False)
    return root
