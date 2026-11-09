"""Shared scrape/rescore workflow used by CLI, systemd and dashboard actions.

The pipeline decides the order of work; adapters own source formats, the
repository owns persistence, and the matcher owns score computation/reuse.
"""
from __future__ import annotations

from collections.abc import Sequence
import logging
import time
from uuid import uuid4

from .adapters.base import ScrapeBatch
from .adapters.registry import discover_adapters
from .domain import JobListing, utcnow
from .locking import run_lock
from .matching import MatchingService
from .observability import log_context, log_failure
from .resume import load_resume

log = logging.getLogger(__name__)


def _score_job(repository, matcher, job, stats, force=False):
    """Reuse a current job assessment or calculate one and persist it safely."""
    context = {"job_id": job["_id"]}
    if job.get("company_id"):
        context["company_id"] = job["company_id"]
    with log_context(**context):
        existing = job.get("match", {})
        # A valid embedded match avoids even a cache database read. Deferred AI
        # results are deliberately retried, even when their content key matches.
        if not force and existing.get("cache_key") == matcher.cache_key(job) and not existing.get("retryable"):
            stats["cache_hits"] += 1
            log.debug("job_match_reused source=stored_job")
            return
        match, cached = matcher.score(job, force=force)
        repository.set_match(job["_id"], job["content_hash"], match)
        log.debug("job_match_saved cached=%s method=%s ai_status=%s",
                  cached, match["method"], match["ai_status"])
        stats["cache_hits" if cached else "scored"] += 1
        if match["retryable"]:
            stats["ai_deferred"] += 1
            if match["ai_status"] == "failed":
                stats["ai_failures"] += 1


def execute(repository, settings, *, kind="scrape", company_id=None, force=False,
            adapters=None, resume_loader=load_resume, client_factory=None, matcher_factory=MatchingService):
    """Run one workflow and return its history ID, status and counters.

    Collaborators are injected so tests can exercise orchestration without real
    websites, PDFs, MongoDB or paid models. Successful writes survive a later
    failure; this workflow is not a transaction spanning every external system.
    """
    if kind not in {"scrape", "rescore"}:
        raise ValueError("Unknown pipeline operation")
    adapters = discover_adapters() if adapters is None else adapters
    if client_factory is None:
        from .scraping.http import ScrapeHTTPClient
        client_factory = ScrapeHTTPClient
    run_id = str(uuid4())
    started = time.monotonic()
    with log_context(run_id=run_id, operation=kind), run_lock(settings.state_dir):
        log.info("run_started force=%s", force)
        try:
            repository.ping()
            repository.ensure_indexes()
            # The shared lock proves no other supported local run owns these rows.
            repository.recover_interrupted_runs()
            companies = repository.companies(company_id, enabled_only=kind == "scrape")
        except Exception as exc:
            log_failure(log, "run_preflight_failed", exc)
            raise
        if company_id and not companies:
            raise ValueError("Company not found or not enabled; implement/configure its adapter first")
        stats = {key: 0 for key in ("seen", "accepted", "excluded", "changed", "unchanged",
                                    "scored", "cache_hits", "ai_deferred", "ai_failures", "company_errors")}
        repository.start_run({"_id": run_id, "kind": kind, "company_id": company_id,
                              "status": "running", "started_at": utcnow()})
        log.info("run_configured companies=%s matching_mode=%s", len(companies), settings.matching_mode)
        try:
            if not companies:
                result = {"status": "success", "stats": stats,
                          "message": "No configured companies. Add companies and implement verified adapters first."}
                repository.finish_run(run_id, result)
                log.info("run_finished status=success reason=no_configured_companies elapsed_ms=%.1f",
                         (time.monotonic() - started) * 1000)
                return {"run_id": run_id, **result}
            resume = resume_loader(settings.resume_path)
            matcher = matcher_factory(repository, resume, settings)
            repository.set_metadata("resume", {"content_hash": resume.content_hash, "updated_at": utcnow()})
            if kind == "rescore":
                # Page through recent active records without materializing an unbounded result set.
                offset = 0
                now = utcnow()
                while True:
                    jobs = repository.recent_jobs(now=now, company_id=company_id, limit=100, offset=offset)
                    log.debug("rescore_page_loaded offset=%s count=%s", offset, len(jobs))
                    for job in jobs:
                        _score_job(repository, matcher, job, stats, force)
                    if len(jobs) < 100:
                        break
                    offset += 100
            else:
                for company in companies:
                    with log_context(company_id=company["_id"]):
                        company_started = time.monotonic()
                        log.info("company_scrape_started")
                        child_id = str(uuid4())
                        repository.start_run({"_id": child_id, "kind": "company_scrape", "parent_run_id": run_id,
                                              "company_id": company["_id"], "started_at": utcnow(), "status": "running"})
                        before = stats.copy()
                        try:
                            adapter = adapters.get(company["adapter"])
                            if adapter is None:
                                raise ValueError("Configured adapter is no longer installed")
                            adapter.validate_config(company)
                            with client_factory(settings, adapter.allowed_hosts(company)) as client:
                                batch = adapter.fetch(company, client)
                            if not isinstance(batch, ScrapeBatch) or not isinstance(batch.jobs, Sequence):
                                raise ValueError("Adapter must return a materialized ScrapeBatch")
                            log.info("source_fetch_finished listings=%s complete_snapshot=%s",
                                     len(batch.jobs), batch.complete_snapshot)
                            # Reject conflicting duplicates before any job writes. Picking
                            # one arbitrarily could discard date/content evidence.
                            unique = {}
                            for listing in batch.jobs:
                                if not isinstance(listing, JobListing):
                                    raise ValueError("Adapter returned an invalid listing")
                                key = listing.identity(company["_id"])
                                if key in unique:
                                    previous = unique[key]
                                    previous_date = previous.posting_date.posted_at if previous.posting_date else None
                                    current_date = listing.posting_date.posted_at if listing.posting_date else None
                                    if previous.content_hash != listing.content_hash or previous_date != current_date:
                                        raise ValueError("Conflicting duplicate listing; adapter must reconcile the source")
                                else:
                                    unique[key] = listing
                            stats["seen"] += len(batch.jobs)
                            log.debug("batch_deduplicated unique=%s duplicates=%s",
                                      len(unique), len(batch.jobs) - len(unique))
                            seen_ids = set()
                            for listing in unique.values():
                                now = utcnow()
                                if listing.posting_date is None or not listing.posting_date.recent(now):
                                    stats["excluded"] += 1
                                    log.debug("job_excluded job_identity=%s reason=%s",
                                              listing.identity(company["_id"]),
                                              "missing_date" if listing.posting_date is None else "outside_window_or_future")
                                    existing_id = repository.exclude_listing(company["_id"], listing, now)
                                    if existing_id:
                                        seen_ids.add(existing_id)
                                    continue
                                job, changed = repository.upsert_job(company, listing, now)
                                log.debug("job_accepted job_identity=%s content_changed=%s", job["_id"], changed)
                                if job["_id"] in seen_ids:
                                    continue
                                seen_ids.add(job["_id"])
                                stats["accepted"] += 1
                                stats["changed" if changed else "unchanged"] += 1
                                _score_job(repository, matcher, job, stats)
                            # Absence only proves closure after a complete successful
                            # snapshot; a partial response must never close unseen jobs.
                            if batch.complete_snapshot:
                                repository.mark_missing(company["_id"], seen_ids)
                                log.debug("missing_jobs_reconciliation_requested seen=%s", len(seen_ids))
                            child_stats = {key: stats[key] - before[key] for key in stats}
                            repository.finish_run(child_id, {"status": "partial" if child_stats["ai_deferred"] else "success",
                                                                  "stats": child_stats})
                            log.info("company_scrape_finished elapsed_ms=%.1f stats=%s",
                                     (time.monotonic() - company_started) * 1000, child_stats)
                        except Exception as exc:
                            # A failed/partial fetch never closes absent jobs. Other companies continue.
                            stats["company_errors"] += 1
                            log_failure(log, "company_scrape_failed", exc)
                            repository.finish_run(child_id, {"status": "failed", "error_type": type(exc).__name__,
                                                            "stats": {key: stats[key] - before[key] for key in stats}})
            result = {"status": "partial" if stats["company_errors"] or stats["ai_deferred"] else "success",
                      "stats": stats, "ai_calls": matcher.calls}
            repository.finish_run(run_id, result)
            log.log(logging.WARNING if result["status"] == "partial" else logging.INFO,
                    "run_finished status=%s elapsed_ms=%.1f ai_calls=%s stats=%s",
                    result["status"], (time.monotonic() - started) * 1000, matcher.calls, stats)
            return {"run_id": run_id, **result}
        except Exception as exc:
            # Record the error in logs first: if MongoDB itself is down, persisting
            # failed history may also raise, but the journal still has this event.
            log_failure(log, "run_failed", exc)
            repository.finish_run(run_id, {"status": "failed", "error_type": type(exc).__name__, "stats": stats})
            raise
        except KeyboardInterrupt:
            # Leave unfinished history available for the existing recovery step.
            log.warning("run_interrupted elapsed_ms=%.1f", (time.monotonic() - started) * 1000)
            raise
