import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from jobscrap.config import Settings
from jobscrap.domain import utcnow

HAS_WEB = all(importlib.util.find_spec(name) is not None for name in ("fastapi", "httpx", "jinja2", "pymongo"))


@unittest.skipUnless(HAS_WEB, "Install runtime dependencies for dashboard tests")
class WebTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = Settings(resume_path=Path(self.directory.name) / "missing.pdf")
        self.repo = MagicMock()
        self.repo.companies.return_value = []
        self.repo.recent_jobs.return_value = []
        self.repo.recent_runs.return_value = []

    def client(self, **kwargs):
        from fastapi.testclient import TestClient
        from jobscrap.web.app import create_app
        return TestClient(create_app(settings=kwargs.pop("settings", self.settings), **kwargs))

    def test_unconfigured_dashboard_is_honest_and_health_is_unready(self):
        with self.client() as client:
            response = client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertIn("MongoDB is not configured", response.text)
            self.assertIn("No sample listings", response.text)
            self.assertEqual(client.get("/healthz").status_code, 503)

    def test_auth_required_when_configured(self):
        from dataclasses import replace
        settings = replace(self.settings, dashboard_username="user", dashboard_password="pass")
        with self.client(settings=settings) as client:
            self.assertEqual(client.get("/").status_code, 401)
            self.assertEqual(client.get("/", auth=("user", "pass")).status_code, 200)

    def test_form_without_csrf_rejected(self):
        with self.client(repository=self.repo) as client:
            self.assertEqual(client.post("/actions/scrape", data={}).status_code, 403)

    def test_manual_action_calls_background_pipeline(self):
        runner = MagicMock(return_value={"status": "success"})
        with self.client(repository=self.repo, runner=runner) as client:
            client.get("/")
            token = client.cookies["jobscrap_csrf"]
            response = client.post("/actions/rescore", data={"csrf": token}, follow_redirects=False)
            self.assertEqual(response.status_code, 303)
        runner.assert_called_once_with("rescore")

    def test_cross_origin_action_rejected(self):
        with self.client(repository=self.repo) as client:
            client.get("/")
            response = client.post("/actions/scrape", data={"csrf": client.cookies["jobscrap_csrf"]},
                                   headers={"Origin": "https://evil.example.test"})
            self.assertEqual(response.status_code, 403)

    def test_db_outage_shows_actionable_message(self):
        from jobscrap.storage import StorageUnavailable
        self.repo.companies.side_effect = StorageUnavailable("internal")
        with self.client(repository=self.repo) as client:
            response = client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertIn("temporarily unavailable", response.text)
            self.assertNotIn("internal", response.text)

    def test_unscored_job_renders_and_html_is_escaped(self):
        self.repo.recent_jobs.return_value = [{
            "_id": "fixture", "company_name": "Fixture", "title": "<script>alert(1)</script>",
            "description": "Python", "location": "Remote", "posted_at": utcnow(),
            "application_url": "https://example.test/job", "content_hash": "v1",
            "posting_evidence": {"field_name": "datePosted", "kind": "ats_api", "precision": "timestamp",
                                 "source_url": "https://example.test/api/job"},
        }]
        with self.client(repository=self.repo) as client:
            response = client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertIn("Pending rescore", response.text)
            self.assertNotIn("<script>alert", response.text)
