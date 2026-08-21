"""Safe user-scoped integration installation."""

from .files import InstallationError, apply, inspect_install, inspect_revert
from .plan import Action, FileState, Plan, StateKind, plan_install, plan_revert

__all__ = [
    "Action",
    "FileState",
    "InstallationError",
    "Plan",
    "StateKind",
    "apply",
    "inspect_install",
    "inspect_revert",
    "plan_install",
    "plan_revert",
]
