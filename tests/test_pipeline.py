from contextlib import nullcontext
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from jobscrap.adapters.base import ScrapeBatch
from jobscrap.config import Settings
from jobscrap.domain import DateKind, JobListing, PostingDate, utcnow
from jobscrap.pipeline import execute
from jobscrap.resume import Resume

COMPANY = {"_id": "fixture", "name": "Fixture company", "adapter": "fixture", "enabled": True}
RESUME = Resume.from_text("Python AWS MongoDB backend API development and distributed systems. " * 4)


def fixture_listing(**changes):
    posted = PostingDate(utcnow() - timedelta(hours=1), "fixture", "https://example.test/job/1",
                         "datePosted", DateKind.ATS_API)
    return JobListing(**({"title": "Python engineer", "description": "Python AWS backend APIs",
                          "location": "Remote", "application_url": "https://example.test/job/1",
                          "posting_date": posted, "external_job_id": "1"} | changes))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = Settings(state_dir=Path(self.directory.name))
        self.repo = MagicMock()
        self.repo.companies.return_value = [COMPANY]
        self.repo.get_cached_score.return_value = None
        self.repo.upsert_job.side_effect = lambda company, listing, now: ({
            "_id": listing.identity(company["_id"]), "title": listing.title,
            "description": listing.description, "location": listing.location,
            "content_hash": listing.content_hash,
        }, True)
        self.adapter = MagicMock()
        self.adapter.allowed_hosts.return_value = {"example.test"}

    def run_pipeline(self, **kwargs):
        return execute(self.repo, self.settings, adapters={"fixture": self.adapter},
                       resume_loader=lambda _: RESUME, client_factory=lambda *args: nullcontext(None), **kwargs)

    def test_unverified_old_and_future_excluded_before_insertion(self):
        old = PostingDate(utcnow() - timedelta(days=4), "fixture", "https://example.test/job/1",
                          "datePosted", DateKind.ATS_API)
        future = PostingDate(utcnow() + timedelta(days=1), "fixture", "https://example.test/job/1",
                             "datePosted", DateKind.ATS_API)
        self.adapter.fetch.return_value = ScrapeBatch([
            fixture_listing(posting_date=None, external_job_id="unverified", application_url="https://example.test/job/unverified"),
            fixture_listing(posting_date=old, external_job_id="old", application_url="https://example.test/job/old"),
            fixture_listing(posting_date=future, external_job_id="future", application_url="https://example.test/job/future"),
            fixture_listing(),
        ])
        result = self.run_pipeline()
        self.assertEqual(result["stats"]["excluded"], 3)
        self.assertEqual(self.repo.upsert_job.call_count, 1)
        self.assertEqual(self.repo.set_match.call_count, 1)
        self.assertEqual(result["status"], "success")

    def test_duplicate_rows_score_once(self):
        job = fixture_listing()
        self.adapter.fetch.return_value = ScrapeBatch([job, job])
        result = self.run_pipeline()
        self.assertEqual(self.repo.set_match.call_count, 1)
        self.assertEqual(result["stats"]["accepted"], 1)
        self.assertEqual(self.repo.upsert_job.call_count, 1)

    def test_conflicting_duplicate_rows_fail_before_storage(self):
        from dataclasses import replace
        job = fixture_listing()
        self.adapter.fetch.return_value = ScrapeBatch([job, replace(job, description="Conflicting source data")], True)
        with self.assertLogs("jobscrap.pipeline", level="ERROR"):
            result = self.run_pipeline()
        self.assertEqual(result["status"], "partial")
        self.repo.upsert_job.assert_not_called()
        self.repo.mark_missing.assert_not_called()

    def test_only_complete_successful_snapshots_close_missing_jobs(self):
        self.adapter.fetch.return_value = ScrapeBatch([fixture_listing()], complete_snapshot=False)
        self.run_pipeline()
        self.repo.mark_missing.assert_not_called()
        self.adapter.fetch.return_value = ScrapeBatch([fixture_listing()], complete_snapshot=True)
        self.run_pipeline()
        self.repo.mark_missing.assert_called_once()

    def test_source_failure_recorded_and_never_closes_jobs(self):
        self.adapter.fetch.side_effect = TimeoutError("Source timeout")
        with self.assertLogs("jobscrap.pipeline", level="ERROR"):
            result = self.run_pipeline()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["stats"]["company_errors"], 1)
        self.repo.mark_missing.assert_not_called()
        self.assertTrue(any(call.args[1].get("error_type") == "TimeoutError"
                            for call in self.repo.finish_run.call_args_list))

    def test_no_enabled_company_is_a_truthful_noop(self):
        self.repo.companies.return_value = []
        result = self.run_pipeline()
        self.assertIn("No configured companies", result["message"])
        self.adapter.fetch.assert_not_called()

    def test_rescore_uses_stored_recent_jobs_without_scraping(self):
        job, _ = self.repo.upsert_job(COMPANY, fixture_listing(), utcnow())
        self.repo.recent_jobs.return_value = [job]
        self.run_pipeline(kind="rescore")
        self.adapter.fetch.assert_not_called()
        self.repo.set_match.assert_called_once()

    def test_crashed_history_recovered_under_run_lock(self):
        self.adapter.fetch.return_value = ScrapeBatch([])
        self.run_pipeline()
        self.repo.recover_interrupted_runs.assert_called_once()

    def test_unchanged_current_score_is_not_reprocessed(self):
        from jobscrap.matching import MatchingService
        job, _ = self.repo.upsert_job(COMPANY, fixture_listing(), utcnow())
        matcher = MatchingService(self.repo, RESUME, self.settings)
        job["match"] = {"cache_key": matcher.cache_key(job), "retryable": False}
        self.repo.upsert_job.side_effect = None
        self.repo.upsert_job.return_value = job, False
        self.adapter.fetch.return_value = ScrapeBatch([fixture_listing()])
        result = self.run_pipeline()
        self.repo.set_match.assert_not_called()
        self.repo.get_cached_score.assert_not_called()
        self.assertEqual(result["stats"]["unchanged"], 1)
