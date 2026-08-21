"""Benchmark the installed hook without recording prompt-derived data."""

from __future__ import annotations

import argparse
import json
import math
import platform
import subprocess
import time
from pathlib import Path

SAFE_INPUT = b'{"hook_event_name":"UserPromptSubmit","prompt":"Explain merge sort."}'
BLOCK_INPUT = (
    b'{"hook_event_name":"UserPromptSubmit","prompt":"Contact alice@example.com"}'
)
BLOCK_OUTPUT = (
    b'{"decision":"block","reason":"SHIM Guard blocked this prompt: EMAIL (1).'
    b'\\nReview and resubmit this typed redacted suggestion:\\nContact <EMAIL_1>"}'
)
HOOK_COMMAND = ("-I", "-B", "-m", "shim_guard.hook")
HOOK_TIMEOUT_SECONDS = 35
DEFAULT_P95_CEILING_MS = 5_000.0


def percentile(samples: list[float], fraction: float) -> float:
    """Return the nearest-rank percentile from positive sample data."""
    if not samples:
        raise ValueError("samples are required")
    return sorted(samples)[math.ceil(len(samples) * fraction) - 1]


def summary(samples: list[float]) -> dict[str, float]:
    return {
        "p50_ms": round(percentile(samples, 0.50), 3),
        "p95_ms": round(percentile(samples, 0.95), 3),
        "max_ms": round(max(samples), 3),
    }


def run_hook(python: Path, payload: bytes, expected: bytes, raw: bytes) -> float:
    started = time.perf_counter()
    try:
        result = subprocess.run(
            (str(python), *HOOK_COMMAND),
            input=payload,
            capture_output=True,
            check=False,
            timeout=HOOK_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("hook benchmark could not run") from error
    elapsed_ms = (time.perf_counter() - started) * 1_000
    if (
        result.returncode != 0
        or result.stdout != expected
        or result.stderr
        or (raw and raw in result.stdout + result.stderr)
    ):
        raise RuntimeError("hook benchmark contract failed")
    return elapsed_ms


def installed_python_version(python: Path) -> str:
    try:
        result = subprocess.run(
            (str(python), "-c", "import platform; print(platform.python_version())"),
            capture_output=True,
            check=False,
            text=True,
            timeout=HOOK_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("installed Python could not report its version") from error
    if result.returncode or not result.stdout.strip():
        raise RuntimeError("installed Python could not report its version")
    return result.stdout.strip()


def benchmark(
    python: Path, samples_per_fixture: int, p95_ceiling_ms: float
) -> dict[str, object]:
    if samples_per_fixture < 1 or p95_ceiling_ms <= 0:
        raise ValueError("samples and p95 ceiling must be positive")
    safe_samples: list[float] = []
    block_samples: list[float] = []
    for _ in range(samples_per_fixture):
        safe_samples.append(run_hook(python, SAFE_INPUT, b"", b""))
        block_samples.append(
            run_hook(python, BLOCK_INPUT, BLOCK_OUTPUT, b"alice@example.com")
        )
    timings = {"safe": summary(safe_samples), "block": summary(block_samples)}
    if any(values["p95_ms"] > p95_ceiling_ms for values in timings.values()):
        raise RuntimeError("hook benchmark exceeded the p95 ceiling")
    return {
        "schema_version": 1,
        "sample_counts": {"safe": len(safe_samples), "block": len(block_samples)},
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "python": installed_python_version(python),
        "p95_ceiling_ms": p95_ceiling_ms,
        "timings_ms": timings,
    }


def self_check() -> None:
    samples = [float(value) for value in range(1, 21)]
    assert percentile(samples, 0.50) == 10.0
    assert percentile(samples, 0.95) == 19.0
    assert summary(samples)["max_ms"] == 20.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "python", nargs="?", type=Path, help="Installed Python executable"
    )
    parser.add_argument("--samples-per-fixture", type=int, default=20)
    parser.add_argument("--p95-ceiling-ms", type=float, default=DEFAULT_P95_CEILING_MS)
    parser.add_argument("--output", type=Path, help="Write JSON to this path")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    if args.python is None:
        parser.error("python is required unless --self-check is used")
    try:
        result = benchmark(args.python, args.samples_per_fixture, args.p95_ceiling_ms)
    except (RuntimeError, ValueError):
        raise SystemExit("hook benchmark failed") from None
    output = json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
    if args.output is None:
        print(output, end="")
    else:
        args.output.write_text(output, encoding="utf-8")


if __name__ == "__main__":
    main()
