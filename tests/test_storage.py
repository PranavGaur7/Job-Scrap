"""Repository contract using mongomock; Atlas coverage is separately opt-in."""
from datetime import timedelta
import importlib.util
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from jobscrap.domain import DateKind, JobListing, PostingDate, utcnow

HAS_MONGO = importlib.util.find_spec("pymongo") is not None
HAS_MOCK = importlib.util.find_spec("mongomock") is not None
COMPANY = {"_id": "fixture", "name": "Fixture company"}


def fixture_job(**changes):
    date = PostingDate(utcnow() - timedelta(hours=1), "fixture", "https://example.test/job/1",
                       "datePosted", DateKind.ATS_API)
    return JobListing(**({"title": "Python engineer", "description": "Python AWS APIs", "location": "Remote",
                          "application_url": "https://example.test/job/1", "posting_date": date,
                          "external_job_id": "1"} | changes))


@unittest.skipUnless(HAS_MONGO and HAS_MOCK, "Install .[test] for repository tests")
class RepositoryTests(unittest.TestCase):
    def setUp(self):
        import mongomock
        from jobscrap.storage import MongoRepository
        self.repo = MongoRepository(mongomock.MongoClient(tz_aware=True).db)
        self.repo.ensure_indexes()

    def test_indexes_enforce_company_scoped_ids_and_urls(self):
        indexes = self.repo.db.jobs.index_information()
        self.assertTrue(indexes["company_external_id_unique"]["unique"])
        self.assertEqual(indexes["company_external_id_unique"]["partialFilterExpression"],
                         {"external_job_id": {"$type": "string"}})
        self.assertTrue(indexes["company_url_unique"]["unique"])

    def test_upsert_preserves_first_seen_and_advances_last_seen(self):
        now = utcnow()
        job = fixture_job()
        one, changed = self.repo.upsert_job(COMPANY, job, now)
        two, changed_again = self.repo.upsert_job(COMPANY, job, now + timedelta(minutes=1))
        self.assertEqual(self.repo.db.jobs.count_documents({}), 1)
        self.assertEqual(one["first_seen_at"], two["first_seen_at"])
        self.assertGreater(two["last_seen_at"], one["last_seen_at"])
        self.assertNotEqual(two["posted_at"], two["first_seen_at"])
        self.assertTrue(changed)
        self.assertFalse(changed_again)

    def test_stable_id_survives_url_change(self):
        one, _ = self.repo.upsert_job(COMPANY, fixture_job())
        two, _ = self.repo.upsert_job(COMPANY, fixture_job(application_url="https://example.test/new-url"))
        self.assertEqual(one["_id"], two["_id"])
        self.assertEqual(self.repo.db.jobs.count_documents({}), 1)

    def test_url_fallback_promoted_to_id_without_duplicate(self):
        one, _ = self.repo.upsert_job(COMPANY, fixture_job(external_job_id=None))
        two, _ = self.repo.upsert_job(COMPANY, fixture_job())
        self.assertEqual(one["_id"], two["_id"])
        self.assertEqual(two["external_job_id"], "1")

    def test_company_names_do_not_determine_identity(self):
        self.repo.upsert_job(COMPANY, fixture_job())
        self.repo.upsert_job({"_id": "another", "name": "Other"}, fixture_job())
        self.assertEqual(self.repo.db.jobs.count_documents({}), 2)

    def test_material_changes_clear_previous_score(self):
        one, _ = self.repo.upsert_job(COMPANY, fixture_job())
        self.repo.set_match(one["_id"], one["content_hash"], {"score": 70})
        unchanged, changed = self.repo.upsert_job(COMPANY, fixture_job())
        self.assertFalse(changed)
        self.assertIn("match", unchanged)
        updated, changed = self.repo.upsert_job(COMPANY, fixture_job(description="Rust Kotlin"))
        self.assertTrue(changed)
        self.assertNotIn("match", updated)
        self.assertEqual(updated["previous_content_hash"], one["content_hash"])

    def test_conflicting_ats_id_on_same_url_is_not_silently_merged(self):
        from jobscrap.storage import IdentityConflict
        self.repo.upsert_job(COMPANY, fixture_job())
        with self.assertRaises(IdentityConflict):
            self.repo.upsert_job(COMPANY, fixture_job(external_job_id="different"))

    def test_expiry_and_retracted_evidence_hide_stored_jobs(self):
        job = fixture_job()
        self.repo.upsert_job(COMPANY, job)
        self.assertEqual(len(self.repo.recent_jobs()), 1)
        self.assertEqual(self.repo.recent_jobs(now=utcnow() + timedelta(days=4)), [])
        self.repo.exclude_listing(COMPANY["_id"], fixture_job(posting_date=None), utcnow())
        self.assertEqual(self.repo.recent_jobs(), [])

    def test_snapshot_missing_jobs_are_deactivated(self):
        self.repo.upsert_job(COMPANY, fixture_job())
        self.repo.mark_missing(COMPANY["_id"], [])
        self.assertEqual(self.repo.recent_jobs(), [])

    def test_compare_and_set_rejects_stale_match(self):
        one, _ = self.repo.upsert_job(COMPANY, fixture_job())
        self.repo.upsert_job(COMPANY, fixture_job(description="Rust only"))
        self.repo.set_match(one["_id"], one["content_hash"], {"score": 99})
        self.assertNotIn("match", self.repo.db.jobs.find_one({"_id": one["_id"]}))

    def test_cache_upserts_and_history_recovers_interruption(self):
        self.repo.cache_score({"_id": "key", "score": 1})
        self.repo.cache_score({"_id": "key", "score": 2})
        self.assertEqual(self.repo.db.match_cache.count_documents({}), 1)
        self.assertEqual(self.repo.get_cached_score("key")["score"], 2)
        self.repo.start_run({"_id": "run", "status": "running", "started_at": utcnow()})
        self.repo.recover_interrupted_runs()
        self.assertEqual(self.repo.recent_runs()[0]["status"], "interrupted")

    def test_transient_database_retries_are_bounded(self):
        from pymongo.errors import AutoReconnect
        from jobscrap.storage import StorageUnavailable
        with patch.object(self.repo.db.match_cache, "find_one", side_effect=AutoReconnect("temporary")) as find:
            with patch("jobscrap.storage.time.sleep"), self.assertLogs("jobscrap.storage", level="WARNING"):
                with self.assertRaises(StorageUnavailable):
                    self.repo.get_cached_score("key")
        self.assertEqual(find.call_count, 3)


@unittest.skipUnless(HAS_MONGO and bool(os.getenv("MONGODB_TEST_URI")), "Set MONGODB_TEST_URI for a real disposable Atlas test")
class AtlasIntegrationTests(unittest.TestCase):
    def test_real_indexes_upserts_and_tls_connectivity(self):
        from jobscrap.config import Settings
        from jobscrap.storage import connect
        name = "jobscrap_test_" + uuid4().hex
        repo = connect(Settings(mongodb_uri=os.environ["MONGODB_TEST_URI"], mongodb_database=name))
        try:
            repo.ping()
            repo.ensure_indexes()
            one, _ = repo.upsert_job(COMPANY, fixture_job(external_job_id=None))
            two, _ = repo.upsert_job(COMPANY, fixture_job())
            self.assertEqual(one["_id"], two["_id"])
            self.assertEqual(repo.db.jobs.count_documents({}), 1)
            self.assertEqual(len(repo.recent_jobs()), 1)
        finally:
            # Only this randomly named test-owned database is removed.
            repo.client.drop_database(name)
            repo.close()
