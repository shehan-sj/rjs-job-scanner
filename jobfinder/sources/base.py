"""Abstract base class for all job sources.

To add a new source:
  1. Create a new file in jobfinder/sources/ (e.g. my_source.py)
  2. Subclass JobSource and set `name = "my_source"`
  3. Implement fetch()
  4. Import it in registry.py
  5. Add a matching entry in config.yaml
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..matcher.models import JobPosting
from ..config import SourceConfig


class JobSource(ABC):
    """Base class for all job board scrapers."""

    #: Must match the 'name' field in config.yaml sources list
    name: str = ""

    @abstractmethod
    def fetch(self, source_config: SourceConfig) -> list[JobPosting]:
        """Fetch job postings for this source configuration.

        Args:
            source_config: The source-specific config block from config.yaml.

        Returns:
            List of normalized JobPosting objects.
        """
        ...
