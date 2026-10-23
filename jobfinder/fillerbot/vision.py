"""Claude Vision integration — analyze page screenshots and plan form actions.

Each call sends a screenshot + the FillerProfile to Claude, which returns a
structured PageAnalysis describing what it sees and what actions to take.
The profile is delivered as a cached system block for efficiency across
multiple calls within the same session.
"""

from __future__ import annotations

import base64
import json
import logging
import anthropic

from ..web.redact import redact_text
from .models import FillAction, FillerProfile, PageAnalysis

logger = logging.getLogger(__name__)

_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "page_state": {
            "type": "string",
            "enum": ["form", "confirmation", "error", "complete", "captcha", "login_required"],
        },
        "page_description": {"type": "string"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action_type": {
                        "type": "string",
                        "enum": ["fill", "click", "select", "check", "upload", "scroll"],
                    },
                    "selector_strategy": {
                        "type": "string",
                        "enum": ["label", "placeholder", "id", "name", "text", "position"],
                    },
                    "selector_value": {"type": "string"},
                    "value": {"type": "string"},
                    "field_label": {"type": "string"},
                    "confidence": {"type": "number"},
                    "source_field": {"type": "string"},
                    "field_index": {"type": "integer"},
                },
                "required": [
                    "action_type",
                    "selector_strategy",
                    "selector_value",
                    "value",
                    "field_label",
                    "confidence",
                    "source_field",
                    "field_index",
                ],
                "additionalProperties": False,
            },
        },
        "next_step": {
            "type": "string",
            "enum": ["fill", "next_page", "review", "submit", "done", "needs_human"],
        },
        "human_note": {"type": "string"},
    },
    "required": ["page_state", "page_description", "actions", "next_step", "human_note"],
    "additionalProperties": False,
}


_SYSTEM_PROMPT = """\
You are FillerBot — an AI assistant that fills job application forms using \
the candidate's profile data.

You receive:
1. A screenshot of a web page
2. The candidate's structured profile
3. A DOM field map — a list of every form field on the page with its HTML \
   attributes (id, name, type, label, current value, select options).

Your job:
1. Use the screenshot to understand the page layout and state.
2. Use the DOM field map to identify each field's unique selector (id or name).
3. For each field, determine the best value from the candidate's profile.
4. Return structured actions using the field's id or name as selector — these \
   are unique and reliable even when labels repeat across sections.

Rules:
- Selector strategy priority: "id" if the field has an id, else "name" if it \
  has a name, else "label" or "placeholder" as fallback.
- ALWAYS use the exact id or name string from the DOM field map — never invent \
  or guess attribute values.
- For dropdowns (select), set action_type to "select" and value to the option \
  text that best matches. The DOM field map includes available options.
- For checkboxes/radio, set action_type to "check" and value to "true"/"false".
- For file upload fields (resume/CV), set action_type to "upload" and \
  source_field to "resume_file".
- For "Next", "Continue", or pagination buttons, set action_type to "click" \
  with next_step "next_page". Use "text" strategy with the button text.
- NEVER click "Submit", "Apply", or final submission buttons — set next_step \
  to "submit" and let the user decide.
- Set confidence between 0 and 1. Below 0.5 means you're guessing.
- If the page shows a CAPTCHA, login wall, or error, report it in page_state \
  and set next_step to "needs_human".
- source_field should name the FillerProfile field used (e.g. "first_name", \
  "work_experience[0].company") or empty string if not from the profile.
- For fields not in the profile (e.g. "How did you hear about us?"), set \
  source_field to "" and confidence to 0, and describe what's needed in \
  human_note.
- Skip fields that already have the correct value (check currentValue in the \
  DOM field map).
- field_index: set to 0 unless using "label" or "placeholder" strategy where \
  the same text appears multiple times — then use 0-based occurrence index.
- For repeated sections (work experience, education), include the section \
  number in field_label for clarity, e.g. "Job title (2nd position)".
- Keep page_description to one short sentence.
- Return actions in top-to-bottom order as they appear on the page."""


class PageAnalyzer:
    """Sends screenshots to Claude Vision and returns structured actions."""

    def __init__(self, api_key: str, model: str, profile: FillerProfile):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._profile = profile
        self._profile_block = self._render_profile(profile)

    @staticmethod
    def _render_profile(profile: FillerProfile) -> str:
        """Render the profile as a compact text block for the system prompt."""
        lines = [
            "## Candidate Profile",
            f"- Name: {profile.full_name}",
            f"- First: {profile.first_name} | Last: {profile.last_name}",
            f"- Email: {profile.email}",
            f"- Phone: {profile.phone}",
            f"- Location: {profile.city}, {profile.state_province}, {profile.zip_postal}, {profile.country}",
            f"- Address: {profile.address}",
            f"- LinkedIn: {profile.linkedin_url}",
            f"- Website: {profile.website}",
            f"- Current title: {profile.current_title}",
            f"- Years experience: {profile.years_experience}",
            f"- Summary: {profile.summary}",
            "",
            "### Work Experience",
        ]
        for i, w in enumerate(profile.work_experience):
            end = w.end_date or "Present"
            lines.append(f"{i + 1}. {w.title} @ {w.company} ({w.start_date} – {end})")
            lines.append(f"   Location: {w.location}")
            lines.append(f"   {w.description}")

        lines.append("")
        lines.append("### Education")
        for e in profile.education:
            lines.append(f"- {e.degree} in {e.field_of_study} — {e.institution} ({e.start_date} – {e.end_date})")
            if e.gpa:
                lines.append(f"  GPA: {e.gpa}")

        lines.append("")
        lines.append(f"### Skills: {', '.join(profile.skills)}")
        if profile.certifications:
            lines.append(f"### Certifications: {', '.join(profile.certifications)}")
        if profile.languages:
            lines.append(f"### Languages: {', '.join(profile.languages)}")

        return "\n".join(lines)

    def analyze(
        self,
        screenshot_png: bytes,
        context: str = "",
        dom_fields: list[dict] | None = None,
    ) -> PageAnalysis:
        """Analyze a page screenshot and return planned actions."""
        img_b64 = base64.b64encode(screenshot_png).decode("ascii")

        user_content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": img_b64,
                },
            },
        ]

        # Build the DOM field map text
        if dom_fields:
            lines = ["## DOM Field Map (from page HTML)"]
            for f in dom_fields:
                parts = [f"tag={f['tag']}"]
                if f.get("type"):
                    parts.append(f"type={f['type']}")
                if f.get("id"):
                    parts.append(f'id="{f["id"]}"')
                if f.get("name"):
                    parts.append(f'name="{f["name"]}"')
                if f.get("label"):
                    parts.append(f'label="{f["label"]}"')
                if f.get("placeholder"):
                    parts.append(f'placeholder="{f["placeholder"]}"')
                # Password values are never sent to the model — there's no
                # legitimate reason FillerBot needs them, and a login
                # password should not leave the machine (the product spec:
                # minimize what's sent to Anthropic on every loop iteration).
                if f.get("type") == "password":
                    parts.append("value=(redacted)")
                else:
                    val = f.get("currentValue", "")
                    parts.append(f'value="{val}"' if val else "value=(empty)")
                if f.get("options"):
                    opts = f["options"][:20]  # cap to avoid huge payloads
                    parts.append(f"options=[{', '.join(opts)}]")
                lines.append(f"- {' | '.join(parts)}")
            user_content.append({"type": "text", "text": "\n".join(lines)})

        if context:
            user_content.append({"type": "text", "text": context})
        else:
            user_content.append(
                {
                    "type": "text",
                    "text": "Analyze this page. Use the DOM field map to identify each "
                    "field by its id or name, then return actions to fill them "
                    "with the candidate's data. Skip fields already correctly filled.",
                }
            )

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=[
                    {"type": "text", "text": _SYSTEM_PROMPT},
                    {
                        "type": "text",
                        "text": self._profile_block,
                        "cache_control": {"type": "ephemeral"},
                    },
                ],
                messages=[{"role": "user", "content": user_content}],
                output_config={
                    "format": {"type": "json_schema", "schema": _ANALYSIS_SCHEMA},
                },
            )
        except Exception as exc:
            # The Anthropic SDK's exception messages can echo request
            # headers verbatim — redact before this
            # reaches a log file (logger.error) or an API response field
            # (human_note), not after.
            safe_detail = redact_text(str(exc))
            logger.error("Vision analysis failed: %s", safe_detail)
            return PageAnalysis(
                page_state="error",
                page_description=f"API error: {safe_detail}",
                next_step="needs_human",
                human_note=safe_detail,
            )

        text = next((b.text for b in response.content if b.type == "text"), "{}")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.error("Vision returned unparseable JSON")
            return PageAnalysis(
                page_state="error",
                page_description="Unparseable response from AI",
                next_step="needs_human",
            )

        actions = []
        for a in data.get("actions", []):
            a.setdefault("field_index", 0)
            actions.append(FillAction(**a))
        return PageAnalysis(
            page_state=data.get("page_state", "form"),
            page_description=data.get("page_description", ""),
            actions=actions,
            next_step=data.get("next_step", "fill"),
            human_note=data.get("human_note", ""),
        )
