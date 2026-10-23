"""Pins the contract between ``local_matcher.py`` and the job panel's score
breakdown (``frontend/src/components/scoreBreakdown.ts``).

The panel used to render ``MatchResult.reasoning`` verbatim -- for a
local-matcher row that is this matcher's own debug line::

    title:30  skills:21 (0 from LinkedIn + 7 from desc)  tools:9 (5)  seniority:0  function:0

The redesign parses that line back into five labelled meters, because the
structured form (``MatchResult.signals``) does not survive to the board:
``HistoryCardDTO`` sets ``signals={}`` as there is no signals column on
``match_history``.

That parse is a cross-language contract with nothing holding it together at
runtime, which is exactly the kind that rots silently -- the UI would just
stop showing a breakdown, with no error anywhere. So this file asserts both
halves against the real code: the axis weights the TS file hard-codes, and
the shape of the string the TS file parses. Change either side and this
names the other.

Same approach as ``tests/test_theme_css_tokens.py`` (parses the real CSS)
and ``tests/test_settings_parity_check.py`` (walks the real Config against
the real ``frontend/src``) -- read the other language's source, don't
re-type its values into an expectation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from jobfinder.matcher import local_matcher

REPO_ROOT = Path(__file__).resolve().parent.parent
TS_PATH = REPO_ROOT / "frontend" / "src" / "components" / "scoreBreakdown.ts"

# The Python side's weights, by the axis key the reasoning line uses.
PYTHON_WEIGHTS = {
    "title": local_matcher._W_TITLE,  # noqa: SLF001
    "skills": local_matcher._W_SKILLS,  # noqa: SLF001
    "tools": local_matcher._W_TOOLS,  # noqa: SLF001
    "seniority": local_matcher._W_SENIORITY,  # noqa: SLF001
    "function": local_matcher._W_FUNCTION,  # noqa: SLF001
}


@pytest.fixture(scope="module")
def ts_source() -> str:
    assert TS_PATH.is_file(), f"{TS_PATH} is missing -- did the module move?"
    return TS_PATH.read_text(encoding="utf-8")


def _parse_ts_axes(source: str) -> dict[str, int]:
    """Pull ``{ key: "title", label: "Title", max: 30 }`` entries out of the
    TS ``SCORE_AXES`` array. A deliberately dumb regex over one
    hand-maintained literal -- not a general TS parser."""
    block = re.search(r"SCORE_AXES[^=]*=\s*\[(.*?)\];", source, re.DOTALL)
    assert block is not None, "could not find the SCORE_AXES array in scoreBreakdown.ts"
    entries = re.findall(r'\{\s*key:\s*"([a-z]+)"\s*,\s*label:\s*"[^"]+"\s*,\s*max:\s*(\d+)\s*\}', block.group(1))
    return {key: int(max_pts) for key, max_pts in entries}


def test_the_ts_axis_weights_match_the_python_matcher(ts_source: str) -> None:
    assert _parse_ts_axes(ts_source) == PYTHON_WEIGHTS, (
        "scoreBreakdown.ts's SCORE_AXES has drifted from local_matcher.py's "
        "_W_* constants -- the panel's meters would be out of the wrong totals"
    )


def test_the_weights_still_sum_to_100(ts_source: str) -> None:
    """Both sides, independently -- the score they explain is 0-100."""
    assert sum(PYTHON_WEIGHTS.values()) == 100
    assert sum(_parse_ts_axes(ts_source).values()) == 100


def _real_reasoning_line() -> str:
    """Build the reasoning string the same way ``score_job`` does, from
    representative point values. Kept beside the real f-string (which is
    not separately callable) so a format change there fails here."""
    title_pts, skills_pts, tools_pts, seniority_pts, function_pts = 30, 21, 9, 0, 0
    explicit_hits: list[str] = []
    desc_hits = ["a", "b", "c", "d", "e", "f", "g"]
    matched_tools = ["t1", "t2", "t3", "t4", "t5"]
    return (
        f"title:{int(title_pts)}  skills:{int(skills_pts)} "
        f"({len(explicit_hits)} from LinkedIn + {len(desc_hits)} from desc)  "
        f"tools:{int(tools_pts)} ({len(matched_tools)})  "
        f"seniority:{int(seniority_pts)}  function:{int(function_pts)}"
    )


def test_the_reasoning_format_in_this_file_matches_the_matcher_source() -> None:
    """Guards ``_real_reasoning_line`` above against going stale: the f-string
    it reproduces must still be the one in ``local_matcher.py``."""
    source = Path(local_matcher.__file__).read_text(encoding="utf-8")
    for fragment in (
        'f"title:{int(title_pts)}  skills:{int(skills_pts)} "',
        'f"({len(explicit_hits)} from LinkedIn + {len(desc_hits)} from desc)  "',
        'f"tools:{int(tools_pts)} ({len(matched_tools)})  "',
        'f"seniority:{int(seniority_pts)}  function:{int(function_pts)}"',
    ):
        assert fragment in source, (
            f"local_matcher.py's reasoning format changed ({fragment!r} not found) -- "
            "frontend/src/components/scoreBreakdown.ts parses this string and must be updated too"
        )


def test_every_axis_is_findable_by_the_ts_parsers_own_pattern() -> None:
    """Runs the TS parser's own per-axis regex, transliterated, against a
    real reasoning line. The TS side returns null unless *every* axis
    matches, so a format change that drops one silently removes the whole
    breakdown from the UI."""
    line = _real_reasoning_line()
    for axis in PYTHON_WEIGHTS:
        # scoreBreakdown.ts: new RegExp(`\\b${axis.key}:\\s*(\\d+)`)
        assert re.search(rf"\b{axis}:\s*(\d+)", line), f"{axis!r} is not parseable out of {line!r}"


def test_the_skills_split_and_tool_count_are_parseable() -> None:
    line = _real_reasoning_line()
    skills = re.search(r"\(\s*(\d+)\s+from\s+LinkedIn\s*\+\s*(\d+)\s+from\s+desc\s*\)", line, re.IGNORECASE)
    tools = re.search(r"\btools:\s*\d+\s*\(\s*(\d+)\s*\)", line, re.IGNORECASE)
    assert skills is not None and skills.group(1) == "0" and skills.group(2) == "7"
    assert tools is not None and tools.group(1) == "5"


def test_a_real_scored_job_produces_a_parseable_reasoning_line() -> None:
    """End to end against the actual scorer, not a reconstructed string."""
    from jobfinder.matcher.models import JobPosting
    from jobfinder.resume.profiler import ResumeProfile

    # Same shape tests/test_local_matcher.py's own `_profile` fixture uses --
    # `score_job` accepts a MatchProfile "or object" with these attributes.
    profile = ResumeProfile(
        job_titles=["QA Automation Engineer"],
        skills=["python", "selenium", "cypress"],
        tools=["jenkins", "docker"],
        seniority_levels=["Senior"],
        years_experience=5,
    )
    job = JobPosting(
        job_id="score-1",
        title="Senior QA Automation Engineer",
        company="Globex",
        location="Remote",
        description="We need python, selenium and cypress experience. Jenkins and docker a plus.",
        url="https://example.com/score-1",
        source="indeed",
    )

    result = local_matcher.score_job(profile, job)

    for axis in PYTHON_WEIGHTS:
        assert re.search(rf"\b{axis}:\s*(\d+)", result.reasoning), (
            f"{axis!r} missing from a real scored job's reasoning: {result.reasoning!r}"
        )
    # And the axes the TS side recovers must add up to the score it explains.
    recovered = sum(int(re.search(rf"\b{axis}:\s*(\d+)", result.reasoning).group(1)) for axis in PYTHON_WEIGHTS)
    assert abs(recovered - result.score) <= 1, (
        f"the parsed axes ({recovered}) and the reported score ({result.score}) disagree by more than rounding; "
        f"the panel's breakdown would not add up to its own headline number. reasoning={result.reasoning!r}"
    )


def test_the_ts_module_is_wired_into_the_panel() -> None:
    """The breakdown existing but not being rendered is the failure mode this
    whole redesign was fixing, so pin the import."""
    panel = (REPO_ROOT / "frontend" / "src" / "components" / "JobPanel.tsx").read_text(encoding="utf-8")
    assert "scoreBreakdown" in panel, "JobPanel.tsx no longer imports the score breakdown"
    # The JSX render form specifically -- the identifier itself still appears
    # in the file's own docstring, which is where the decision is explained.
    assert "{card.reasoning}" not in panel, (
        "JobPanel.tsx renders card.reasoning directly again -- that is the raw matcher debug "
        "line; it must go through reasoningProse()/scoreBreakdown()"
    )


def test_json_round_trip_of_the_axis_table_is_stable(ts_source: str) -> None:
    """A cheap snapshot so a reordering shows up in a diff as a value change
    rather than only as a passing test."""
    assert json.dumps(_parse_ts_axes(ts_source), sort_keys=True) == json.dumps(PYTHON_WEIGHTS, sort_keys=True)
