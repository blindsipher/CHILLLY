"""TopstepX backend package.

This package contains loosely coupled services that collectively provide an
event‑driven trading backend. Subpackages are organized by domain: configuration
helpers live under :mod:`config`, networking clients under :mod:`networking`,
and trading logic under :mod:`strategy`. All communication happens over the
central :class:`~topstepx_backend.core.event_bus.EventBus` so that individual
services can be developed and tested in isolation.

The package is designed to be imported by the system orchestrator but the
modules are also usable independently for experimentation or unit testing.
"""

__all__ = [
    "config",
    "auth",
    "core",
    "data",
    "networking",
    "services",
    "strategy",
    "utils",
]
