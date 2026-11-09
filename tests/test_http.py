import importlib.util
import unittest

from jobscrap.config import Settings

HAS_HTTPX = importlib.util.find_spec("httpx") is not None


@unittest.skipUnless(HAS_HTTPX, "Install runtime dependencies for transport tests")
class HTTPTests(unittest.TestCase):
    def client(self, handler, **kwargs):
        import httpx
        from jobscrap.scraping.http import ScrapeHTTPClient
        return ScrapeHTTPClient(Settings(request_interval=0), {"example.test"},
                                transport=httpx.MockTransport(handler), sleep=lambda _: None, **kwargs)

    def test_robots_disallow_blocks_job_request(self):
        import httpx
        from jobscrap.scraping.http import AccessRestricted
        seen = []

        def handler(request):
            seen.append(request.url.path)
            return httpx.Response(200, text="User-agent: *\nDisallow: /jobs")
        with self.client(handler) as client, self.assertRaises(AccessRestricted):
            client.get("https://example.test/jobs")
        self.assertEqual(seen, ["/robots.txt"])

    def test_transient_response_retried(self):
        import httpx
        count = 0

        def handler(request):
            nonlocal count
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            count += 1
            return httpx.Response(503 if count < 3 else 200, json={"ok": True})
        with self.client(handler) as client:
            self.assertEqual(client.get_json("https://example.test/jobs"), {"ok": True})
        self.assertEqual(count, 3)

    def test_robots_failure_fails_closed(self):
        import httpx
        with self.client(lambda request: httpx.Response(403)) as client:
            from jobscrap.scraping.http import AccessRestricted
            with self.assertRaises(AccessRestricted):
                client.get("https://example.test/jobs")

    def test_redirect_cannot_escape_declared_hosts(self):
        import httpx
        from jobscrap.scraping.http import AccessRestricted

        def handler(request):
            return httpx.Response(404) if request.url.path == "/robots.txt" else httpx.Response(
                302, headers={"location": "https://undeclared.test/jobs"})
        with self.client(handler) as client, self.assertRaises(AccessRestricted):
            client.get("https://example.test/jobs")

    def test_long_retry_after_defers_to_later_run(self):
        import httpx
        from jobscrap.scraping.http import AccessRestricted

        def handler(request):
            return httpx.Response(404) if request.url.path == "/robots.txt" else httpx.Response(
                429, headers={"Retry-After": "3600"})
        with self.client(handler) as client, self.assertRaises(AccessRestricted):
            client.get("https://example.test/jobs")

    def test_response_size_limit(self):
        import httpx
        from jobscrap.scraping.http import AccessRestricted
        with self.client(lambda request: httpx.Response(200, text="x" * 100)) as client:
            client.settings = Settings(max_response_bytes=10)
            with self.assertRaises(AccessRestricted):
                client.get("https://example.test/jobs")
