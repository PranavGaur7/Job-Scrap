from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from jobscrap.domain import DateKind, JobListing, PostingDate, canonical_url

NOW = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)


def posting(value="2026-11-08T08:00:00Z", **kwargs):
    return PostingDate.from_iso(value, source_url="https://careers.example.test/jobs/42",
                                field_name="datePosted", kind=DateKind.ATS_API, **kwargs)


def listing(**kwargs):
    values = dict(title="Python engineer", location="Remote", description="Python and AWS APIs",
                  application_url="https://careers.example.test/jobs/42", external_job_id="42",
                  posting_date=posting())
    return JobListing(**(values | kwargs))


class DateTests(unittest.TestCase):
    def test_rolling_window_boundaries(self):
        for offset, accepted in [(0, True), (-72, True), (-72.001, False), (0.001, False)]:
            value = replace(posting(), posted_at=NOW + timedelta(hours=offset))
            self.assertEqual(value.recent(NOW), accepted)

    def test_offsets_converted_to_utc(self):
        self.assertEqual(posting("2026-11-08T13:30:00+05:30").posted_at, posting().posted_at)

    def test_naive_requires_known_timezone(self):
        with self.assertRaises(ValueError):
            posting("2026-11-08T13:30:00")
        self.assertEqual(posting("2026-11-08T13:30:00", source_timezone="Asia/Kolkata").posted_at,
                         posting().posted_at)

    def test_date_only_conservatively_excludes_boundary_day(self):
        self.assertFalse(posting("2026-11-05", source_timezone="UTC").recent(NOW))
        self.assertTrue(posting("2026-11-06", source_timezone="UTC").recent(NOW))
        self.assertEqual(posting("2026-11-06", source_timezone="UTC").precision, "date")

    def test_relative_ambiguous_missing_dates_rejected(self):
        for raw in ("3 days ago", "08/11/26", "", "Nov 8", "1731000000", "2026-11-08garbage"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                posting(raw)

    def test_update_and_discovery_dates_rejected(self):
        for field in ("updated_at", "dateModified", "first_seen_at", "last_seen", "scrapedAt"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                replace(posting(), field_name=field)

    def test_evidence_required(self):
        for change in ({"raw_value": ""}, {"source_url": "javascript:bad"}, {"field_name": ""},
                       {"posted_at": datetime(2026, 11, 8)}, {"kind": "inferred"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(posting(), **change)

    def test_dst_ambiguity_and_nonexistent_times_rejected(self):
        for raw in ("2026-11-01T01:30:00", "2026-03-08T02:30:00"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                posting(raw, source_timezone="America/New_York")


class IdentityTests(unittest.TestCase):
    def test_tracking_and_fragment_removed(self):
        self.assertEqual(canonical_url("https://JOBS.example.test:443/Jobs?a=1&utm_source=x&jobId=42#apply"),
                         "https://jobs.example.test/Jobs?a=1&jobId=42")

    def test_functional_parameters_preserved(self):
        self.assertNotEqual(canonical_url("https://example.test/?jobId=1"),
                            canonical_url("https://example.test/?jobId=2"))
        self.assertIn("source=internal", canonical_url("https://example.test/?source=internal"))

    def test_stable_ids_are_company_scoped(self):
        job = listing()
        self.assertNotEqual(job.identity("one"), job.identity("two"))
        self.assertEqual(job.identity("one"), replace(job, application_url="https://example.test/new").identity("one"))

    def test_missing_id_falls_back_to_normalized_url(self):
        one = listing(external_job_id=None)
        two = replace(one, application_url=one.application_url + "?utm_medium=email")
        self.assertEqual(one.identity("one"), two.identity("one"))
        self.assertIsNone(listing(external_job_id="  ").external_job_id)

    def test_content_hash_ignores_dates_tracking_and_whitespace(self):
        one = listing(description="<p>Python   and AWS APIs</p>")
        self.assertEqual(one.content_hash, listing().content_hash)
        self.assertEqual(one.content_hash, replace(one, posting_date=None).content_hash)
        self.assertNotEqual(one.content_hash, replace(one, description="Rust APIs").content_hash)
        self.assertNotEqual(one.content_hash, replace(one, location="London").content_hash)

    def test_unsafe_urls_and_empty_jobs_rejected(self):
        for url in ("javascript:alert(1)", "https://user:pass@example.test/", "https://example.test/\nhi"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                listing(application_url=url)
        with self.assertRaises(ValueError):
            listing(description=" ")
