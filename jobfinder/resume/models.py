from dataclasses import dataclass


@dataclass
class ResumeData:
    text: str  # Full extracted text
    file_path: str  # Source file path
    mtime: float  # File modification time (for cache invalidation)

    def truncated(self, max_chars: int = 6000) -> str:
        """Return resume text truncated to max_chars."""
        if len(self.text) <= max_chars:
            return self.text
        return self.text[:max_chars] + "\n\n[Resume truncated for length]"
