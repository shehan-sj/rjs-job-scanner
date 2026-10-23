"""T51 fix (b) — the extraction prompt must not presuppose a profession.

the product spec calls this the subtle one. ``resume/profiler.py``'s system prompt
was seeded with one field's few-shot examples ("QA Engineer", "SDET",
"Selenium", "Cypress"), so the *agentic* extraction path was shown QA-shaped
output and asked to produce more of it, whatever the résumé said. A profile
cannot be correct if it comes from a prompt that presupposes the answer, and
nothing downstream in the matcher can recover a profile that was wrong when it
was written.

Why these are text assertions
-----------------------------
Evaluating a prompt change properly means calling the model. The whole suite
makes **zero** billed Anthropic requests (tests/conftest.py stubs
``messages.create``/``stream``/``models.list``) and a real key is configured on
developer machines, so a live call here would be billed for real. These tests
therefore assert the prompt's *text*: that no profession's vocabulary appears
in it, that it still states the output contract in full, and that it tells the
model where to get its terms. What they cannot assert is extraction quality —
see the module note in ``profiler.py`` for why output *structure* is not at
risk (it is enforced by a json_schema output format, not by example).
"""

from __future__ import annotations

import re

import pytest

from jobfinder.resume.profiler import _PROFILE_SCHEMA, _SYSTEM_PROMPT

#: Several professions, not only QA. Asserting the absence of one profession's
#: words would be satisfied by swapping it for another's; asserting the absence
#: of six is the property actually wanted.
_PROFESSION_VOCABULARY = {
    "quality assurance": ("qa", "qe", "sdet", "quality assurance", "selenium", "cypress", "regression testing"),
    "nursing": ("nurse", "nursing", "triage", "phlebotomy", "bedside"),
    "culinary": ("chef", "sous chef", "butchery", "plating", "mise en place"),
    "legal": ("paralegal", "deposition", "e-discovery", "docketing"),
    "marketing": ("seo", "copywriting", "demand generation", "lead nurturing"),
    "software": ("ci/cd", "kubernetes", "postman", "jira", "jenkins", "docker"),
}


def _contains_word(haystack: str, term: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", haystack) is not None


@pytest.mark.parametrize("profession", sorted(_PROFESSION_VOCABULARY))
def test_prompt_contains_no_profession_vocabulary(profession):
    lowered = _SYSTEM_PROMPT.lower()
    found = [term for term in _PROFESSION_VOCABULARY[profession] if _contains_word(lowered, term)]
    assert not found, f"{profession} vocabulary leaked into the extraction prompt: {found}"


def test_prompt_still_states_the_whole_output_contract():
    """Removing the examples must not remove the instructions.

    Every field the schema requires has to still be described by name, or the
    prompt would be neutral and useless rather than neutral and correct.
    """
    missing = [field for field in _PROFILE_SCHEMA["required"] if field not in _SYSTEM_PROMPT]
    assert not missing, f"the prompt no longer describes: {missing}"


def test_prompt_tells_the_model_to_derive_terms_from_this_resume():
    """The replacement for few-shot examples is an explicit instruction.

    "No examples" on its own would be a weaker prompt, not a neutral one. The
    instruction has to say where terms come from and that an empty field beats
    a guessed one.
    """
    lowered = _SYSTEM_PROMPT.lower()
    assert "résumé" in lowered or "resume" in lowered
    assert "only" in lowered or "solely" in lowered
    assert "do not assume a field" in lowered


def test_prompt_still_forbids_slash_compounds_without_a_worked_example():
    """A format rule kept, illustrated abstractly rather than with real terms.

    The old prompt taught this rule with two tools from one field. The rule is
    load-bearing — ``_normalize_terms`` splits on "/" when scoring — so it has
    to survive the removal of the examples that taught it.
    """
    assert "slash-separated" in _SYSTEM_PROMPT
    assert '"A/B"' in _SYSTEM_PROMPT


def test_output_structure_does_not_depend_on_the_prompt():
    """Why dropping the examples cannot break the result's shape.

    Structure comes from a json_schema output format. This test pins that the
    schema is still the thing enforcing it, so a future reader does not restore
    few-shot examples believing they hold the output together.
    """
    assert _PROFILE_SCHEMA["additionalProperties"] is False
    assert set(_PROFILE_SCHEMA["required"]) == set(_PROFILE_SCHEMA["properties"])
