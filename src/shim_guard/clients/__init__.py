"""Client-native adapters."""

from pkgutil import iter_modules

CLIENT_NAMES = tuple(
    sorted(
        module.name
        for module in iter_modules(__path__)
        if module.ispkg and not module.name.startswith("_")
    )
)
