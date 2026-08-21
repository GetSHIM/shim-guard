"""Safe user-scoped file installation."""

from .files import InstallationError, apply, inspect_file
from .plan import Action, FileState, Plan, StateKind, plan_change

__all__ = [
    "Action",
    "FileState",
    "InstallationError",
    "Plan",
    "StateKind",
    "apply",
    "inspect_file",
    "plan_change",
]
