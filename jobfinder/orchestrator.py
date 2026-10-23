"""Scan cycle coordinator — fetches jobs, scores them, triggers alerts."""

from __future__ import annotations

import logging
import queue
import uuid
from typing import Protocol

from . import derived_profile as derived_profile_model
from .config import AgenticFilterConfig, Config, LocationFilterConfig
from .filters import _classify_tier
from .matcher.claude_matcher import ClaudeMatcher
from .matcher.local_matcher import score_job
from .matcher.location_parser import matches_region_list
from .matcher.models import JobPosting, MatchResult
from .matcher.profile import GENERIC_TITLE_WORDS, MatchProfile
from .resume.profiler import ResumeProfile, load_profile
from .sources.registry import build_active_sources
from .storage.database import Database, company_matches_skip

logger = logging.getLogger(__name__)


def _title_is_relevant(job: JobPosting, profile: MatchProfile | object) -> bool:
    """Quick relevance gate for non-LinkedIn sources.

    Requires EITHER a distinctive word from the user's own target titles in the
    posting's title, OR at least two meaningful (non-generic) word overlaps
    with those titles. Single generic words like 'Manager' or 'Engineer' alone
    are not enough.

    This gate runs **before** scoring, so its vocabulary matters more than the
    scorer's: anything it drops is never scored at all. It used to wave through
    any title containing a QA keyword and judge everything else on overlap,
    which meant a nurse's postings from a non-LinkedIn source could be
    discarded while QA postings always survived. The domain
    signal is now the user's own (T51).
    """
    if job.source == "linkedin":
        return True

    profile = MatchProfile.coerce(profile)
    title_lower = job.title.lower()

    # Strong signal: a term from the user's own targets that the overlap pass
    # below is blind to — a short acronym ("RN", "CPA"), or a multi-word domain
    # phrase. Longer words are left to the overlap rule, which already judges
    # them and does so more strictly.
    if profile.title_carries_term_overlap_cannot_see(title_lower):
        return True

    # Check target title overlap — require 2+ non-generic word matches
    for target in profile.job_titles:
        target_words = {w for w in target.lower().split() if len(w) > 2}
        meaningful = target_words - GENERIC_TITLE_WORDS
        generic = target_words & GENERIC_TITLE_WORDS
        meaningful_hits = sum(1 for w in meaningful if w in title_lower)
        generic_hits = sum(1 for w in generic if w in title_lower)
        # At least one meaningful word + one any word, or 2+ meaningful
        if meaningful_hits >= 1 and (meaningful_hits + generic_hits) >= 2:
            return True

    return False


def agent_may_remove(current_stage: str) -> bool:
    """Whether the agentic filter may auto-remove a job in ``current_stage``.

    Only out of ``new``. The product spec: scans fill New and
    nothing else; everything to the right of it waits for a user action.
    A re-scan that could pull a shortlisted or applied job back out of
    the pipeline would make the board untrustworthy as a record of what
    the user did, which is the whole reason the board is worth having.

    Extracted as a function rather than left as an ``if`` inside the scan
    loop so the rule is testable without standing up an orchestrator
    (migration invariant 6.2).
    """
    return current_stage == "new"


def _check_geo(job: JobPosting, cfg: LocationFilterConfig) -> str | None:
    """Return a warning string if the job's location/work-type is unfavorable, else None."""
    arr = job.work_arrangement  # "Remote" | "Hybrid" | "On-site" | "Unknown"
    loc_lower = (job.location or "").strip().lower()

    # No preferred regions configured means no geographic preference to be
    # outside of — and since T48 made empty the shipped default (the product spec),
    # this is the fresh-install path. Without this guard every
    # non-remote job would be labelled "On-site · outside " with nothing
    # after the word.
    if not cfg.preferred_regions:
        return None

    # Fully remote jobs are always OK when remote_ok_worldwide is set
    if arr == "Remote" and cfg.remote_ok_worldwide:
        return None

    # Structured match on preferred city/region
    if matches_region_list(job.city, job.region, cfg.preferred_regions):
        return None

    # Fallback substring match on the original location string
    if loc_lower and any(r.lower() in loc_lower for r in cfg.preferred_regions):
        return None

    if not loc_lower:
        return None

    if arr == "Remote":
        if cfg.remote_ok_worldwide:
            return None
        label = "Remote"
    elif arr in ("Hybrid", "On-site"):
        label = arr
    else:
        label = "Unverified location"

    regions_short = "/".join(cfg.preferred_regions[:2])
    return f"{label} · outside {regions_short}"


class Notifier(Protocol):
    """Structural protocol for anything that can raise a user-visible notification.

    The core must not import Qt (invariant 4), so this protocol —
    not `jobfinder.ui.tray.TrayIcon` — is the Orchestrator's dependency.
    `TrayIcon.notify(self, title: str, message: str) -> None` already matches
    this shape structurally; no change to the Qt side is required.
    """

    def notify(self, title: str, message: str) -> None: ...


class NullNotifier:
    """No-op default `Notifier` so the Orchestrator works with no notifier supplied."""

    def notify(self, title: str, message: str) -> None:
        return None


class Orchestrator:
    def __init__(
        self,
        config: Config,
        db: Database,
        tk_queue: queue.Queue,
        tray: Notifier | None = None,
    ):
        self._config = config
        self._db = db
        self._tk_queue = tk_queue
        self._tray: Notifier = tray if tray is not None else NullNotifier()
        self._profile: ResumeProfile | None = None

    def _get_profile(self) -> ResumeProfile | None:
        try:
            self._profile = load_profile(
                resume_path=self._config.resume.path,
                api_key=self._config.anthropic_api_key,
                model=self._config.anthropic.model,
            )
            return self._profile
        except FileNotFoundError as exc:
            logger.error("Resume not found: %s", exc)
            return None
        except Exception as exc:
            logger.error("Failed to load resume profile: %s", exc)
            return None

    def _get_match_profile(self, profile: ResumeProfile) -> MatchProfile:
        """The matcher's view of the user, resolved **once per scan**.

        T50 made a user's edits authoritative per field, but
        nothing routed matching through that read model: a user who corrected
        their job titles changed what FillerBot typed into forms and nothing
        about which jobs they were shown. Resolving here closes that.

        Resolved once and handed down rather than per job: a per-posting
        resolve would re-read three stores and re-run the heuristic extractor
        for every job in the scan, which is pure waste — the profile cannot
        change mid-scan.

        The freshly loaded ``ResumeProfile`` is passed in rather than re-read,
        because :func:`~jobfinder.derived_profile.load_layers` reads the
        agentic stores from *cache only* (deliberately — a read of that surface
        must not be able to trigger a billed call), and the extraction this
        scan may just have paid for is the newer value.

        A failure to resolve must not abort the scan: matching on the agentic
        profile alone is degraded but correct, whereas not scanning is not.
        """
        try:
            layers = derived_profile_model.load_layers(self._config.resume.path)
            layers["resume_profile"] = profile
            resolved = derived_profile_model.resolve(**layers)
            match_profile = MatchProfile.from_derived(resolved)
            edited = sorted(
                name
                for name in ("job_titles", "skills", "domains", "keywords", "seniority_levels")
                if name in resolved and resolved[name].provenance == derived_profile_model.PROVENANCE_USER
            )
            if edited:
                logger.info("Matching on your own corrections for: %s", ", ".join(edited))
            return match_profile
        except Exception as exc:
            logger.warning(
                "Could not resolve the derived profile (%s) — matching on the extracted profile alone.",
                exc,
            )
            return MatchProfile.from_resume_profile(profile)

    def _build_agentic_matcher(self, profile: ResumeProfile) -> ClaudeMatcher | None:
        """Instantiate ClaudeMatcher if the agentic pass is enabled and an API key is set."""
        af: AgenticFilterConfig = self._config.agentic_filter
        if not af.enabled:
            return None
        api_key = self._config.anthropic_api_key
        if not api_key:
            logger.warning("Agentic filter enabled but no Anthropic API key — skipping verification.")
            return None
        return ClaudeMatcher(api_key=api_key, model=self._config.anthropic.model, profile=profile)

    def run_scan(self) -> None:
        logger.info("─── Scan cycle started ───")
        self._tk_queue.put({"type": "scan_start"})

        # One id per scan cycle (T57) — stamped onto every MatchResult this
        # cycle produces, below, so match_history rows from the same cycle
        # share it regardless of how their individual matched_at timestamps
        # spread out (e.g. one agentic-verification API call per match).
        scan_id = uuid.uuid4().hex

        profile = self._get_profile()
        if profile is None:
            return

        # One resolve per scan; every relevance and scoring call below uses it.
        match_profile = self._get_match_profile(profile)

        source_pairs = build_active_sources(self._config.active_sources)
        if not source_pairs:
            logger.warning("No active sources configured.")
            return

        all_jobs = []
        for source, src_config in source_pairs:
            try:
                jobs = source.fetch(src_config)
                all_jobs.extend(jobs)
            except Exception as exc:
                logger.error("Source '%s' failed: %s", src_config.name, exc)

        if not all_jobs:
            logger.info("No jobs fetched this cycle.")
            self._tk_queue.put({"type": "scan_complete", "new_jobs": 0, "matches": 0, "best_score": None})
            return

        logger.info("Fetched %d jobs total.", len(all_jobs))

        new_jobs = self._db.filter_unseen(all_jobs)

        # Company skip list — drop anything from a company the user muted
        skip_keys = self._db.get_skipped_company_keys()
        if skip_keys:
            before = len(new_jobs)
            new_jobs = [j for j in new_jobs if not company_matches_skip(j.company, skip_keys)]
            skipped = before - len(new_jobs)
            if skipped:
                logger.info("Skipped %d job(s) from companies on your skip list.", skipped)

        if self._config.filter_contracts:
            before = len(new_jobs)
            new_jobs = [j for j in new_jobs if not j.is_contract]
            dropped = before - len(new_jobs)
            if dropped:
                logger.info("Filtered %d contract/temp role(s).", dropped)

        # Drop obviously irrelevant jobs from non-LinkedIn sources
        before = len(new_jobs)
        new_jobs = [j for j in new_jobs if _title_is_relevant(j, match_profile)]
        irrelevant = before - len(new_jobs)
        if irrelevant:
            logger.info("Filtered %d irrelevant title(s) from non-LinkedIn sources.", irrelevant)

        logger.info("%d genuinely new jobs to process.", len(new_jobs))

        self._db.mark_seen(all_jobs)

        if not new_jobs:
            logger.info("All jobs already seen — nothing new.")
            self._tk_queue.put({"type": "scan_complete", "new_jobs": 0, "matches": 0, "best_score": None})
            return

        matches: list[MatchResult] = []
        # T65 — the highest score among *every* job actually scored this
        # cycle, not just the ones that cleared match_threshold. A scan
        # that finds nothing above the threshold still found something;
        # this is what lets the Matches empty state say so (the
        # sparse-state DoD: "name the highest score it did find").
        best_score: int | None = None
        for job in new_jobs:
            try:
                result = score_job(match_profile, job)
                result.geo_warning = _check_geo(job, self._config.location_filter)
                result.tier = _classify_tier(
                    job,
                    self._config.location_filter.preferred_regions,
                    self._config.location_filter.search_country,
                )
                result.scan_id = scan_id
                geo_note = f" ⚠ {result.geo_warning}" if result.geo_warning else ""
                logger.info(
                    "[%3d%%] %s @ %s  [%s · %s]%s",
                    result.score,
                    job.title,
                    job.company,
                    job.work_arrangement,
                    job.employment_type,
                    geo_note,
                )
                if best_score is None or result.score > best_score:
                    best_score = result.score
                if result.score >= self._config.match_threshold:
                    matches.append(result)
            except Exception as exc:
                logger.error("Matching error for '%s': %s", job.title, exc)

        # ── Optional agentic verification pass ────────────────────────────────
        matches = self._run_agentic_pass(profile, matches)

        logger.info(
            "─── Scan complete: %d new jobs, %d above %d%% threshold ───",
            len(new_jobs),
            len(matches),
            self._config.match_threshold,
        )

        self._tk_queue.put(
            {"type": "scan_complete", "new_jobs": len(new_jobs), "matches": len(matches), "best_score": best_score}
        )

        if matches:
            self._alert_batch(matches)

    def _run_agentic_pass(
        self,
        profile: ResumeProfile,
        matches: list[MatchResult],
    ) -> list[MatchResult]:
        """Send qualifying matches through the Claude verifier; drop disqualified jobs."""
        matcher = self._build_agentic_matcher(profile)
        if matcher is None or not matches:
            return matches

        min_score = self._config.agentic_filter.min_local_score
        verified: list[MatchResult] = []
        skipped_low_score = 0
        dropped_disqualified = 0

        for result in matches:
            if result.score < min_score:
                verified.append(result)
                skipped_low_score += 1
                continue
            new_result, disqualifying = matcher.verify(result)
            if disqualifying:
                dropped_disqualified += 1
                logger.info(
                    "Agent disqualified: %s @ %s — %s",
                    new_result.job.title,
                    new_result.job.company,
                    new_result.reasoning,
                )
                # Persist the verified match + mark it removed by the agent so
                # the user can see what was auto-dropped.
                #
                # Guarded on the job still being in `new`: the product spec
                # says everything to the right of New waits for a user action,
                # and rule 1 says a job is in exactly one stage. An unguarded
                # `set_stage` here would let a re-scan silently pull a job the
                # user had already shortlisted or applied to back out of the
                # pipeline; the pre-T79 `mark_removed` was worse still, since
                # it left the job in *both* tables and the removal was then
                # invisible behind `stage_of`'s precedence. Found by the Lead
                # while verifying T80, which is where this call site was
                # flagged as the last writer not going through `set_stage`.
                try:
                    self._db.save_match(new_result)
                    current_stage = self._db.stage_of(new_result.job.job_id)
                    if agent_may_remove(current_stage):
                        self._db.set_stage(new_result.job.job_id, "out", removed_by="agent")
                    else:
                        logger.info(
                            "Agent disqualified '%s' but you had already moved it to %s — leaving it there",
                            new_result.job.title,
                            current_stage,
                        )
                except Exception as exc:
                    logger.warning(
                        "Could not persist agent-disqualified job '%s': %s",
                        new_result.job.title,
                        exc,
                    )
                continue
            new_result.tier = _classify_tier(
                new_result.job,
                self._config.location_filter.preferred_regions,
                self._config.location_filter.search_country,
            )
            new_result.geo_warning = _check_geo(new_result.job, self._config.location_filter)
            verified.append(new_result)

        logger.info(
            "Agent pass: verified %d, skipped %d below %d%%, dropped %d disqualified.",
            len(verified) - skipped_low_score,
            skipped_low_score,
            min_score,
            dropped_disqualified,
        )
        return [r for r in verified if r.score >= self._config.match_threshold]

    def _alert_batch(self, matches: list[MatchResult]) -> None:
        for result in matches:
            self._db.save_match(result)

        top = max(matches, key=lambda r: r.score)
        if len(matches) == 1:
            summary = f"{top.job.title} @ {top.job.company}"
        else:
            summary = f"Top: {top.job.title} ({top.score}%) + {len(matches) - 1} more"
        self._tray.notify(
            title=f"{len(matches)} New Match{'es' if len(matches) > 1 else ''} Found",
            message=summary,
        )

        self._tk_queue.put({"type": "matches", "items": matches})

    def show_recent_matches(self) -> None:
        rows = self._db.get_recent_matches()
        self._tk_queue.put({"type": "show_history", "items": rows})
