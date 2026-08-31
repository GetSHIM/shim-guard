import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

COMMANDS = (
    ("uv", "sync", "--locked"),
    ("uv", "run", "--locked", "ruff", "check", "."),
    ("uv", "run", "--locked", "ruff", "format", "--check", "."),
    ("uv", "run", "--locked", "ty", "check"),
    ("uv", "run", "--locked", "pytest"),
    ("uv", "build", "--no-build-isolation"),
    ("git", "diff", "--check"),
)


if __name__ == "__main__":
    for command in COMMANDS:
        subprocess.run(command, cwd=ROOT, check=True)
