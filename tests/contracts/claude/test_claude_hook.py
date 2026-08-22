from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMMAND = (sys.executable, "-I", "-B", "-m", "shim_guard.hook", "claude")
GENERIC_BLOCK = (
    b'{"decision":"block","reason":"SHIM Guard could not safely inspect this '
    b'prompt. Try again or run `shim scan` locally.",'
    b'"suppressOriginalPrompt":true}'
)
READ_INSTRUCTION = "Read this file and use its contents as my prompt: "


def _run(raw: bytes, tmp_path: Path) -> subprocess.CompletedProcess[bytes]:
    environment = os.environ.copy()
    environment["TMPDIR"] = str(tmp_path)
    return subprocess.run(
        COMMAND,
        input=raw,
        capture_output=True,
        cwd=ROOT,
        env=environment,
        check=False,
        timeout=60,
    )


def _payload(prompt: str) -> bytes:
    return json.dumps(
        {
            "session_id": "session",
            "transcript_path": "/tmp/transcript.jsonl",
            "cwd": "/workspace",
            "permission_mode": "default",
            "hook_event_name": "UserPromptSubmit",
            "prompt": prompt,
        },
        separators=(",", ":"),
    ).encode()


def test_claude_code_runner_allows_safe_prompts_silently(tmp_path: Path) -> None:
    result = _run(_payload("Explain merge sort."), tmp_path)
    assert (result.returncode, result.stdout, result.stderr) == (0, b"", b"")


def test_claude_code_runner_blocks_with_a_private_redaction(tmp_path: Path) -> None:
    result = _run(_payload("Contact alice@example.com"), tmp_path)
    document = json.loads(result.stdout)
    path = Path(document["reason"].split(READ_INSTRUCTION, 1)[1])

    assert result.returncode == 0
    assert result.stderr == b""
    assert document["decision"] == "block"
    assert document["suppressOriginalPrompt"] is True
    assert b"alice@example.com" not in result.stdout
    assert path.read_text() == "Contact <EMAIL_1>"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_claude_code_runner_fails_closed_on_invalid_input(tmp_path: Path) -> None:
    result = _run(b'{"hook_event_name":"UserPromptSubmit"}', tmp_path)
    assert (result.returncode, result.stdout, result.stderr) == (0, GENERIC_BLOCK, b"")
