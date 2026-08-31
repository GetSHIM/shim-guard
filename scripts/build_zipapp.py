"""Build the plugin's self-contained hook archive.

The plugin ships a runnable hook so `/plugin install` alone produces a working
guard, with no separate package-manager step. Only the hook path goes in — the
CLI and its presentation dependencies stay out — plus the parts of
`phonenumbers` the phone recognizer actually uses. Its geocoder, carrier,
short-number and timezone data sets are 20 MB and are never touched by
`PhoneNumberMatcher`, so they are excluded.

    python scripts/build_zipapp.py --output plugins/shim-guard/bin/shim.pyz
"""

from __future__ import annotations

import argparse
import compileall
import os
import shutil
import time
import zipapp
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "src"
PACKAGE = "shim_guard"
# Everything reachable from `shim_guard.hook`. The CLI is deliberately absent.
INCLUDED = (
    "__init__.py",
    "py.typed",
    "config.py",
    "hook.py",
    "policy.py",
    "clients/__init__.py",
    "clients/user_prompt_hook.py",
    "clients/claude/__init__.py",
    "clients/claude/hook.py",
    "clients/claude/tool_events.py",
    "clients/codex/__init__.py",
    "clients/codex/hook.py",
    "clients/copilot/__init__.py",
    "clients/copilot/hook.py",
    "events/__init__.py",
    "events/diet.py",
    "events/injection.py",
    "events/payload.py",
    "events/pipeline.py",
    "guard/__init__.py",
    "guard/analyze.py",
    "guard/entities.py",
    "guard/evaluate.py",
    "guard/iban_patterns.py",
    "guard/models.py",
    "guard/normalize.py",
    "guard/recognizers.py",
    "guard/suffixes.py",
    "session/__init__.py",
    "session/ledger.py",
    "session/record.py",
    "session/spool.py",
    "session/summary.py",
    "settings_files/__init__.py",
    "settings_files/files.py",
    "settings_files/plan.py",
)
# The geocoder, carrier and timezone data sets are 19 MB between them and are
# only reachable through modules the recognizer never imports. shortdata cannot
# be dropped wholesale because phonenumbers/__init__ imports shortnumberinfo,
# but its 241 per-region tables are loaded lazily and only by short-number
# APIs, which this hook never calls. .pyi stubs have no runtime role.
# Compiled extension modules cannot be imported from inside a zip at all, and
# are specific to one interpreter version and platform, so vendoring them would
# make the archive non-reproducible across build machines for no benefit: the
# pure-Python sources beside them are what actually run.
COMPILED = ("*.so", "*.pyd", "*.dylib")
VENDORED_EXCLUDES = (
    "__pycache__",
    "geodata",
    "carrierdata",
    "tzdata",
    "*.pyi",
) + COMPILED
VENDORED_PRUNE = ("shortdata/region_*.py",)
MAIN = '''"""Entry point for the bundled SHIM Guard hook.

Deliberately written in the subset of Python that both 2.x and 3.x can parse.
The launcher hands this archive to whatever ``python3`` it finds, and an
unsupported interpreter must produce the client's allow response and one line
of explanation rather than a syntax error. Checking the version here instead of
in the launcher also avoids spawning a second process on every hook event.
"""

import sys

MINIMUM = (3, 9)
NOTICE = (
    "shim-guard: needs Python %d.%d or newer; found %s. "
    "The prompt was not inspected.\\n"
)

if sys.version_info[:2] < MINIMUM:
    sys.stderr.write(
        NOTICE % (MINIMUM[0], MINIMUM[1], ".".join(str(p) for p in sys.version_info[:3]))
    )
    sys.exit(0)

from shim_guard.hook import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
'''


def stage(destination: Path, vendor: bool = True) -> Path:
    """Lay out the archive contents and return the staging directory."""
    package = destination / PACKAGE
    for relative in INCLUDED:
        source = SOURCE_ROOT / PACKAGE / relative
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    (destination / "__main__.py").write_text(MAIN, encoding="utf-8")
    if vendor:
        import phonenumbers

        # tomli is the stdlib TOML parser backported; `config.py` prefers
        # `tomllib` and falls back to it below 3.11, which the system
        # interpreter this archive targets is.
        import tomli

        shutil.copytree(
            Path(tomli.__file__).parent,
            destination / "tomli",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyi", *COMPILED),
        )
        vendored = destination / "phonenumbers"
        shutil.copytree(
            Path(phonenumbers.__file__).parent,
            vendored,
            ignore=shutil.ignore_patterns(*VENDORED_EXCLUDES),
        )
        for pattern in VENDORED_PRUNE:
            for path in vendored.glob(pattern):
                path.unlink()
    return destination


def build(
    output: Path, staging: Path, interpreter: str = "/usr/bin/env python3"
) -> Path:
    """Write a reproducible compressed archive and make it executable."""
    # ZIP records local timestamps and Unix mode bits; normalize both.
    timestamp = time.mktime((2000, 1, 1, 0, 0, 0, 0, 1, -1))
    for path in staging.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
        os.utime(path, (timestamp, timestamp))
    output.parent.mkdir(parents=True, exist_ok=True)
    zipapp.create_archive(
        staging, target=output, interpreter=interpreter, compressed=True
    )
    output.chmod(0o755)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--staging", type=Path, help="Keep the staged tree here")
    parser.add_argument("--no-vendor", action="store_true")
    parser.add_argument("--max-bytes", type=int, default=500_000)
    args = parser.parse_args()

    import tempfile

    with tempfile.TemporaryDirectory(prefix="shim-zipapp-") as directory:
        staging = Path(args.staging) if args.staging else Path(directory) / "app"
        staging.mkdir(parents=True, exist_ok=True)
        stage(staging, vendor=not args.no_vendor)
        compileall.compile_dir(str(staging), quiet=2, force=True)
        for cache in staging.rglob("__pycache__"):
            shutil.rmtree(cache)
        build(args.output, staging)

    size = args.output.stat().st_size
    if size > args.max_bytes:
        raise SystemExit(f"{args.output} is {size} bytes, over {args.max_bytes}")
    print(f"{args.output} {size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
