"""Registry that maps source names to JobSource classes."""

from __future__ import annotations

import logging
from typing import Type

from .base import JobSource
from ..config import SourceConfig

logger = logging.getLogger(__name__)

_registry: dict[str, Type[JobSource]] = {}


def register(source_cls: Type[JobSource]) -> Type[JobSource]:
    """Decorator / function to register a JobSource class."""
    if not source_cls.name:
        raise ValueError(f"{source_cls.__name__} must define a non-empty 'name' class attribute.")
    _registry[source_cls.name] = source_cls
    logger.debug("Registered source: %s", source_cls.name)
    return source_cls


def build_active_sources(source_configs: list[SourceConfig]) -> list[tuple[JobSource, SourceConfig]]:
    """Return instantiated (source, config) pairs for all enabled sources.

    Sources whose name isn't in the registry are skipped with a warning.
    """
    # Import all known sources so they self-register
    from . import jobspy_source  # noqa: F401  (LinkedIn, Indeed, Glassdoor, ZipRecruiter)
    from . import remotive_source  # noqa: F401  (free, no key)
    from . import arbeitnow_source  # noqa: F401  (free, no key)
    from . import adzuna_source  # noqa: F401  (free tier, requires app_id + app_key)
    from . import jsearch_source  # noqa: F401  (free tier, requires RapidAPI key)

    pairs: list[tuple[JobSource, SourceConfig]] = []
    for cfg in source_configs:
        if not cfg.enabled:
            continue
        cls = _registry.get(cfg.name)
        if cls is None:
            logger.warning("Unknown source '%s' in config — skipping.", cfg.name)
            continue
        pairs.append((cls(), cfg))
    return pairs
