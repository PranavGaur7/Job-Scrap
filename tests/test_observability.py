"""Behavioral checks for logging context, privacy and cross-module run tracing."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext, redirect_stderr, redirect_stdout
import io
import logging
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

from jobscrap.adapters.base import ScrapeBatch
from jobscrap.config import Settings
from jobscrap.matching import MatchingService
from jobscrap.observability import configure_logging, log_context, log_failure, UTCFormatter
from jobscrap.pipeline import execute
from tests import test_matching as matching_fixtures
from tests import test_pipeline as pipeline_fixtures


class LoggingTestCase(unittest.TestCase):
    """Isolate process-wide logging configuration so tests don't affect each other."""

    def setUp(self):
        self.app_logger = logging.getLogger("jobscrap")
        original = (self.app_logger.handlers[:], self.app_logger.level, self.app_logger.propagate)
        external = {name: logging.getLogger(name).level for name in ("httpx", "httpcore", "pymongo", "openai")}

        def restore():
            for handler in self.app_logger.handlers:
                if handler not in original[0]:
                    handler.close()
            self.app_logger.handlers, self.app_logger.level, self.app_logger.propagate = original
            for name, level in external.items():
                logging.getLogger(name).setLevel(level)

        self.addCleanup(restore)
        self.app_logger.handlers = []
        self.output = io.StringIO()
        with redirect_stderr(self.output):
            configure_logging("DEBUG")
        self.log = logging.getLogger("jobscrap.logging_tests")


class LoggingTests(LoggingTestCase):
    def test_nested_context_is_inherited_and_restored(self):
        with log_context(run_id="run-one", operation="scrape"):
            with log_context(company_id="company-one"):
                self.log.info("inside_company")
            self.log.info("after_company")
        self.log.info("after_run")
        lines = self.output.getvalue().splitlines()
        self.assertIn("run_id=run-one company_id=company-one operation=scrape", lines[0])
        self.assertIn("run_id=run-one company_id=- operation=scrape", lines[1])
        self.assertIn("run_id=- company_id=- operation=-", lines[2])

    def test_context_restores_after_exception(self):
        with self.assertRaises(RuntimeError):
            with log_context(run_id="temporary"):
                raise RuntimeError("fixture")
        self.log.info("outside")
        self.assertIn("run_id=-", self.output.getvalue())

    def test_worker_context_does_not_leak_into_next_task(self):
        def first():
            with log_context(run_id="worker-run"):
                self.log.info("worker_one")

        with log_context(run_id="parent-run"), ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(first).result()
            executor.submit(self.log.info, "worker_two").result()
            self.log.info("parent")
        lines = self.output.getvalue().splitlines()
        self.assertIn("run_id=worker-run", lines[0])
        self.assertIn("run_id=-", lines[1])
        self.assertIn("run_id=parent-run", lines[2])

    def test_logging_configuration_is_idempotent(self):
        with redirect_stderr(self.output):
            configure_logging("INFO")
            configure_logging("INFO")
        self.log.info("one_event")
        self.log.debug("hidden_debug")
        self.assertEqual(self.output.getvalue().count("one_event"), 1)
        self.assertNotIn("hidden_debug", self.output.getvalue())

    def test_logs_leave_stdout_available_for_machine_readable_results(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.log.info("diagnostic")
            print('{"status":"success"}')
        self.assertEqual(stdout.getvalue(), '{"status":"success"}\n')
        self.assertIn("diagnostic", self.output.getvalue())

    def test_timestamp_is_explicit_utc(self):
        record = self.log.makeRecord(self.log.name, logging.INFO, __file__, 0, "epoch", (), None)
        record.created, record.msecs = 0, 0
        self.app_logger.handle(record)
        self.assertTrue(self.output.getvalue().startswith("1970-01-01T00:00:00.000Z"))

    def test_failure_diagnostics_exclude_exception_text_and_locals(self):
        private_value = "PRIVATE_API_KEY_AND_RESUME_TEXT"
        try:
            raise TimeoutError(private_value)
        except TimeoutError as exc:
            log_failure(self.log, "provider_failed", exc)
        output = self.output.getvalue()
        self.assertIn("error_type=TimeoutError", output)
        self.assertIn("test_observability.py:", output)
        self.assertNotIn(private_value, output)
        self.assertNotIn("raise TimeoutError", output)

    def test_exception_formatter_also_protects_standard_exception_calls(self):
        try:
            raise RuntimeError("PRIVATE_CONNECTION_URI")
        except RuntimeError:
            self.log.exception("operation_failed")
        self.assertIn("error_type=RuntimeError", self.output.getvalue())
        self.assertNotIn("PRIVATE_CONNECTION_URI", self.output.getvalue())

    def test_cached_exception_text_cannot_bypass_safe_formatter(self):
        try:
            raise ValueError("PRIVATE_TOKEN")
        except ValueError:
            record = self.log.makeRecord(self.log.name, logging.ERROR, __file__, 0,
                                         "failed", (), sys.exc_info())
        record.exc_text = "PRIVATE_CACHED_TRACEBACK"
        rendered = UTCFormatter("%(message)s").format(record)
        self.assertNotIn("PRIVATE_", rendered)
        self.assertIn("error_type=ValueError", rendered)
        self.assertEqual(record.exc_text, "PRIVATE_CACHED_TRACEBACK")

    def test_context_cannot_inject_extra_log_lines(self):
        with log_context(company_id="one\nforged_record"):
            self.log.info("event")
        self.assertEqual(len(self.output.getvalue().splitlines()), 1)
        self.assertIn(r"company_id=one\nforged_record", self.output.getvalue())

    def test_ai_budget_warning_is_emitted_once_per_service(self):
        service = MatchingService(matching_fixtures.Cache(), matching_fixtures.RESUME,
                                  Settings(matching_mode="hybrid", ai_model="fixture", ai_max_calls=0))
        first, _ = service.score(matching_fixtures.JOB)
        second, _ = service.score(matching_fixtures.JOB | {"content_hash": "changed"})
        self.assertTrue(first["retryable"] and second["retryable"])
        self.assertEqual(self.output.getvalue().count("ai_budget_exhausted"), 1)


class PipelineLoggingTests(LoggingTestCase):
    def run_fixture(self, *, fetch_error=None, preflight_error=None):
        repo = MagicMock()
        repo.companies.return_value = [pipeline_fixtures.COMPANY]
        repo.get_cached_score.return_value = None
        if preflight_error:
            repo.ping.side_effect = preflight_error
        listing = pipeline_fixtures.fixture_listing()
        job = {"_id": listing.identity("fixture"), "title": listing.title, "location": listing.location,
               "description": listing.description, "content_hash": listing.content_hash}
        repo.upsert_job.return_value = job, True
        adapter = MagicMock()
        adapter.allowed_hosts.return_value = {"example.test"}
        adapter.fetch.return_value = ScrapeBatch([listing])
        if fetch_error:
            adapter.fetch.side_effect = fetch_error
        with tempfile.TemporaryDirectory() as directory:
            return execute(repo, Settings(state_dir=Path(directory)), adapters={"fixture": adapter},
                           resume_loader=lambda _: pipeline_fixtures.RESUME,
                           client_factory=lambda *args: nullcontext(None)), job

    def test_run_company_and_job_events_can_be_correlated(self):
        result, job = self.run_fixture()
        output = self.output.getvalue()
        self.assertIn(f"run_id={result['run_id']} company_id=fixture operation=scrape", output)
        self.assertIn(f"job_id={job['_id']} match_cache_miss", output)
        self.assertIn("source_fetch_finished listings=1", output)
        self.assertIn("run_finished status=success elapsed_ms=", output)
        self.assertNotIn(pipeline_fixtures.RESUME.text, output)
        self.assertNotIn(job["description"], output)
        self.assertNotIn("https://example.test/", output)

    def test_source_failure_logs_type_and_context_without_payload(self):
        result, _ = self.run_fixture(fetch_error=TimeoutError("PRIVATE_SOURCE_PAYLOAD"))
        self.assertEqual(result["status"], "partial")
        output = self.output.getvalue()
        self.assertIn("company_scrape_failed error_type=TimeoutError", output)
        self.assertIn("company_id=fixture", output)
        self.assertNotIn("PRIVATE_SOURCE_PAYLOAD", output)

    def test_preflight_failure_is_logged_even_before_database_history_exists(self):
        with self.assertRaises(ConnectionError):
            self.run_fixture(preflight_error=ConnectionError("PRIVATE_MONGODB_URI"))
        output = self.output.getvalue()
        self.assertIn("run_preflight_failed error_type=ConnectionError", output)
        self.assertNotIn("PRIVATE_MONGODB_URI", output)
