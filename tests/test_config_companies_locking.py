from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from jobscrap.companies import validate_company
from jobscrap.config import Settings
from jobscrap.locking import RunBusy, run_lock


class ConfigTests(unittest.TestCase):
    def test_external_binding_requires_auth(self):
        with self.assertRaises(ValueError):
            Settings(dashboard_host="0.0.0.0")
        self.assertEqual(Settings(dashboard_host="0.0.0.0", dashboard_username="u", dashboard_password="p").dashboard_host, "0.0.0.0")

    def test_secret_repr_is_redacted(self):
        self.assertNotIn("secret", repr(Settings(mongodb_uri="secret", dashboard_username="u", dashboard_password="secret")))

    def test_bad_configuration_fails_early(self):
        for change in ({"matching_mode": "invalid"}, {"matching_mode": "hybrid"},
                       {"ai_max_calls": -1}, {"http_attempts": 0}, {"dashboard_username": "alone"},
                       {"request_interval": -1}, {"mongodb_timeout_ms": 0},
                       {"http_timeout": float("inf")}, {"request_interval": float("nan")}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(Settings(), **change)


class CompanyTests(unittest.TestCase):
    value = {"id": "example", "name": "Example", "careers_url": "https://careers.example.test/"}

    def test_pending_company_is_disabled(self):
        company = validate_company(self.value, {})
        self.assertFalse(company["enabled"])
        self.assertEqual(company["integration_status"], "awaiting_adapter")

    def test_unknown_adapter_not_invented(self):
        with self.assertRaises(ValueError):
            validate_company(self.value | {"adapter": "unverified"}, {})

    def test_pending_company_cannot_be_enabled(self):
        with self.assertRaises(ValueError):
            validate_company(self.value | {"enabled": True}, {})

    def test_bad_id_and_secret_url_rejected(self):
        for change in ({"id": "../bad"}, {"name": ""}, {"careers_url": "https://u:p@example.test/"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_company(self.value | change, {})

    def test_nested_adapter_secrets_are_not_persisted(self):
        for config in ({"api_key": "private"}, {"headers": {"Authorization": "private"}},
                       {"nested": [{"access_token": "private"}]}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate_company(self.value | {"adapter_config": config}, {})

    def test_adapter_validates_its_own_configuration(self):
        class Verified:
            def validate_config(self, company):
                if not company["adapter_config"].get("tenant"):
                    raise ValueError("Tenant is required")
        value = self.value | {"adapter": "fixture"}
        with self.assertRaises(ValueError):
            validate_company(value, {"fixture": Verified()})
        company = validate_company(value | {"adapter_config": {"tenant": "example"}}, {"fixture": Verified()})
        self.assertTrue(company["enabled"])


class LockTests(unittest.TestCase):
    def test_lock_excludes_concurrent_runs_and_releases_after_error(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with self.assertRaises(RuntimeError):
                with run_lock(state):
                    with self.assertRaises(RunBusy):
                        with run_lock(state):
                            self.fail("Concurrent acquisition must not succeed")
                    raise RuntimeError("interrupted")
            with run_lock(state):
                pass
