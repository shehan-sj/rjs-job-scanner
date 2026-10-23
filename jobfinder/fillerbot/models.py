"""FillerBot data models — structured profile for form-filling + action types."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WorkEntry:
    company: str = ""
    title: str = ""
    start_date: str = ""
    end_date: str = ""  # empty string means "present"
    location: str = ""
    description: str = ""


@dataclass
class EducationEntry:
    institution: str = ""
    degree: str = ""  # e.g. "Bachelor of Science"
    field_of_study: str = ""  # e.g. "Computer Science"
    start_date: str = ""
    end_date: str = ""
    gpa: str = ""


@dataclass
class FillerProfile:
    """Structured personal data extracted from the resume for form-filling.

    This is separate from ResumeProfile (used for keyword matching). FillerProfile
    holds contact info, work history, and education — the things application forms
    actually ask for.
    """

    # Contact
    full_name: str = ""
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    address: str = ""
    city: str = ""
    state_province: str = ""
    zip_postal: str = ""
    country: str = ""
    linkedin_url: str = ""
    website: str = ""

    # Professional summary
    summary: str = ""
    current_title: str = ""
    years_experience: int = 0

    # Structured history
    work_experience: list[WorkEntry] = field(default_factory=list)
    education: list[EducationEntry] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)

    # Cache metadata
    source_path: str = ""
    source_mtime: float = 0.0
    generated_at: str = ""
    profile_version: str = ""


# ── Action models for the vision→fill loop ───────────────────────────────────


@dataclass
class FillAction:
    """A single action the engine should perform on the page."""

    action_type: str = "fill"  # fill | click | select | check | upload | scroll
    selector_strategy: str = ""  # label | placeholder | id | name | position | text
    selector_value: str = ""
    value: str = ""  # what to type / select / check
    field_label: str = ""  # human-readable label shown in UI
    confidence: float = 1.0  # 0–1
    source_field: str = ""  # which FillerProfile field this maps to
    field_index: int = 0  # 0-based index when multiple fields share a label


@dataclass
class PageAnalysis:
    """Claude Vision's structured assessment of a page screenshot."""

    page_state: str = "form"  # form | confirmation | error | complete | captcha | login_required
    page_description: str = ""
    actions: list[FillAction] = field(default_factory=list)
    next_step: str = "fill"  # fill | next_page | review | submit | done | needs_human
    human_note: str = ""  # explanation when needs_human
