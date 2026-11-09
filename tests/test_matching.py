from dataclasses import replace
import unittest

from jobscrap.config import Settings
from jobscrap.matching import MatchingService, deterministic_match, skills
from jobscrap.resume import Resume, redact_contacts

RESUME = Resume.from_text("Python AWS backend engineer. Built APIs with Java and MongoDB. "
                          "Designed distributed systems with Linux and Docker. " * 3)
JOB = {"title": "Backend engineer", "location": "Remote", "description": "Python AWS Docker APIs", "content_hash": "job-v1"}


class Cache:
    def __init__(self):
        self.values = {}

    def get_cached_score(self, key):
        return self.values.get(key)

    def cache_score(self, score):
        self.values[score["_id"]] = score


class MatchingTests(unittest.TestCase):
    def test_relevant_job_ranks_above_unrelated(self):
        good = deterministic_match(RESUME, JOB["title"], JOB["description"])
        bad = deterministic_match(RESUME, "iOS developer", "Swift Kotlin mobile development")
        self.assertGreater(good.score, bad.score)
        self.assertEqual(good.missing_skills, [])
        self.assertTrue(set(bad.missing_skills) >= {"swift", "kotlin"})
        self.assertTrue(0 <= good.score <= 100)

    def test_aliases_and_word_boundaries(self):
        found = skills("JavaScript React.js C++ notJava Node.js and MySQL")
        self.assertTrue({"javascript", "react", "c++", "node.js", "mysql"} <= found)
        self.assertNotIn("java", found)
        self.assertNotIn("sql", found)

    def test_repetition_cannot_inflate_match(self):
        one = deterministic_match(RESUME, "Engineer", "Python Rust")
        many = deterministic_match(RESUME, "Engineer", "Python Rust " * 100)
        self.assertEqual(one.score, many.score)

    def test_no_known_skill_has_explicit_heuristic_reason(self):
        result = deterministic_match(RESUME, "Baker", "Prepare bread and cakes")
        self.assertIn("keyword", result.reason)
        self.assertEqual(result.score, 0)

    def test_resume_hash_stable_after_whitespace_change(self):
        self.assertEqual(RESUME.content_hash, Resume.from_text(RESUME.text.replace(" ", "\n ")).content_hash)

    def test_empty_resume_rejected(self):
        with self.assertRaises(ValueError):
            Resume.from_text(" ")

    def test_contacts_redacted(self):
        text = redact_contacts("me@example.test +91-9876543210 https://example.test/profile Python")
        self.assertNotIn("987654", text)
        self.assertNotIn("me@", text)
        self.assertIn("Python", text)

    def test_cache_reused_without_backend(self):
        cache = Cache()
        service = MatchingService(cache, RESUME, Settings())
        one, cached = service.score(JOB)
        two, cached_again = service.score(JOB)
        self.assertFalse(cached)
        self.assertTrue(cached_again)
        self.assertEqual(one, two)
        self.assertEqual(service.calls, 0)

    def test_cache_invalidates_on_resume_content_and_configuration(self):
        settings = Settings()
        service = MatchingService(Cache(), RESUME, settings)
        key = service.cache_key(JOB)
        self.assertNotEqual(key, service.cache_key(JOB | {"content_hash": "job-v2"}))
        updated_resume = Resume.from_text(RESUME.text + " New Kotlin project.")
        self.assertNotEqual(key, MatchingService(Cache(), updated_resume, settings).cache_key(JOB))
        hybrid = replace(settings, matching_mode="hybrid", ai_model="test-model")
        self.assertNotEqual(key, MatchingService(Cache(), RESUME, hybrid).cache_key(JOB))

    def test_call_budget_fallback_remains_retryable(self):
        settings = Settings(matching_mode="hybrid", ai_model="test-model", ai_max_calls=0)
        service = MatchingService(Cache(), RESUME, settings)
        result, _ = service.score(JOB)
        self.assertEqual(result["ai_status"], "budget_deferred")
        self.assertTrue(result["retryable"])
        self.assertEqual(service.repository.values, {})

    def test_ai_failure_does_not_poison_cache(self):
        class Failed:
            def __init__(self, settings):
                pass

            def score(self, *args):
                raise TimeoutError("Sensitive provider error")
        service = MatchingService(Cache(), RESUME, Settings(matching_mode="hybrid", ai_model="test"), Failed)
        with self.assertLogs("jobscrap.matching", level="WARNING") as logs:
            result, _ = service.score(JOB)
        self.assertNotIn("Sensitive", " ".join(logs.output))
        self.assertEqual(result["method"], "deterministic")
        self.assertEqual(result["ai_status"], "failed")
        self.assertEqual(service.repository.values, {})

    def test_successful_ai_is_cached_and_budget_counts_calls(self):
        class Successful:
            def __init__(self, settings):
                pass

            def score(self, resume, job, baseline):
                return replace(baseline, score=88, method="ai")
        service = MatchingService(Cache(), RESUME, Settings(matching_mode="hybrid", ai_model="test", ai_max_calls=1), Successful)
        first, _ = service.score(JOB)
        second, hit = service.score(JOB)
        third, _ = service.score(JOB | {"content_hash": "new"})
        self.assertEqual(first["score"], 88)
        self.assertTrue(hit)
        self.assertEqual(second["method"], "ai")
        self.assertEqual(third["ai_status"], "budget_deferred")
        self.assertEqual(service.calls, 1)

    def test_force_bypasses_cache(self):
        service = MatchingService(Cache(), RESUME, Settings())
        service.score(JOB)
        _, hit = service.score(JOB, force=True)
        self.assertFalse(hit)

    def test_prefilter_avoids_paid_calls(self):
        service = MatchingService(Cache(), RESUME, Settings(matching_mode="hybrid", ai_model="test", ai_prefilter_min=100))
        result, _ = service.score(JOB | {"description": "Swift Kotlin"})
        self.assertEqual(result["ai_status"], "prefiltered")
        self.assertEqual(service.calls, 0)
