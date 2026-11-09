"""Reusable source transport: access policy, pacing, bounded bodies and retries.

The client fetches bytes; adapters interpret source fields. Logs intentionally
exclude URL paths/query strings because ATS endpoints may contain sensitive data.
"""
from __future__ import annotations

from email.utils import parsedate_to_datetime
import logging
import time
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from ..domain import http_url, utcnow
from ..observability import log_failure

log = logging.getLogger(__name__)


class AccessRestricted(RuntimeError):
    pass


class ScrapeHTTPClient:
    """Sequential requests, per-host pacing, robots.txt, bounded retries and bodies."""

    def __init__(self, settings, allowed_hosts, *, transport=None, sleep=time.sleep, clock=time.monotonic):
        self.settings = settings
        self.allowed_hosts = {host.lower() for host in allowed_hosts}
        self.sleep, self.clock = sleep, clock
        self.last_request, self.robots = {}, {}
        self.client = httpx.Client(
            timeout=settings.http_timeout, follow_redirects=False, transport=transport,
            headers={"User-Agent": settings.user_agent, "Accept": "application/json,text/html,text/plain"},
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.client.close()

    def _validate(self, url):
        http_url(url)
        parts = urlsplit(url)
        if parts.hostname.lower() not in self.allowed_hosts:
            raise AccessRestricted("Request/redirect host was not declared by the adapter")
        return f"{parts.scheme}://{parts.netloc}"

    def throttle(self, url):
        """Use monotonic time for delays; wall-clock corrections must not skip pacing."""
        origin = self._validate(url)
        robot = self.robots.get(origin)
        crawl_delay = robot.crawl_delay(self.settings.user_agent) if robot else None
        interval = max(self.settings.request_interval, crawl_delay or 0)
        elapsed = self.clock() - self.last_request.get(origin, float("-inf"))
        if elapsed < interval:
            self.sleep(interval - elapsed)
        self.last_request[origin] = self.clock()

    def _retry_delay(self, response, attempt):
        """Honor server backoff, deferring excessively long waits to a later run."""
        value = response.headers.get("Retry-After", "") if response else ""
        try:
            seconds = float(value)
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(value) - utcnow()).total_seconds()
            except (ValueError, TypeError, OverflowError):
                seconds = 2**attempt
        if seconds > 60:
            log.warning("http_retry_deferred reason=server_requested_long_delay delay_seconds=%.1f", seconds)
            raise AccessRestricted("Server requested a longer retry delay; retry in a later run")
        return max(0, seconds)

    def _raw(self, url):
        """Fetch one URL with bounded retries; redirect/robots policy lives above it."""
        self._validate(url)
        for attempt in range(self.settings.http_attempts):
            response = None
            try:
                self.throttle(url)
                started = self.clock()
                with self.client.stream("GET", url) as stream:
                    content = bytearray()
                    for chunk in stream.iter_bytes():
                        content.extend(chunk)
                        if len(content) > self.settings.max_response_bytes:
                            raise AccessRestricted("Response exceeds configured size limit")
                    response = httpx.Response(stream.status_code, headers=stream.headers,
                                              content=bytes(content), request=stream.request)
                log.debug("http_response host=%s status=%s bytes=%s elapsed_ms=%.1f",
                          urlsplit(url).hostname, response.status_code, len(content),
                          (self.clock() - started) * 1000)
                if response.status_code not in {408, 425, 429, 500, 502, 503, 504}:
                    return response
                if attempt == self.settings.http_attempts - 1:
                    log.error("http_status_retries_exhausted host=%s status=%s attempts=%s",
                              urlsplit(url).hostname, response.status_code, self.settings.http_attempts)
                    response.raise_for_status()
            except httpx.TransportError as exc:
                if attempt == self.settings.http_attempts - 1:
                    log_failure(log, "http_network_retries_exhausted", exc)
                    raise
            if attempt < self.settings.http_attempts - 1:
                delay = self._retry_delay(response, attempt)
                log.warning("http_retry host=%s retry=%s limit=%s status=%s delay_seconds=%.1f",
                            urlsplit(url).hostname, attempt + 1, self.settings.http_attempts - 1,
                            response.status_code if response is not None else "network_error", delay)
                self.sleep(delay)
        raise AssertionError("unreachable")

    def _load_robots(self, origin):
        """Cache robots policy per origin; an uncertain policy fails closed."""
        url = f"{origin}/robots.txt"
        for _ in range(6):
            response = self._raw(url)
            if response.is_redirect:
                if not response.headers.get("location"):
                    raise AccessRestricted("Invalid robots redirect")
                url = urljoin(url, response.headers["location"])
                self._validate(url)
                continue
            parser = RobotFileParser()
            if response.status_code in {404, 410}:
                parser.parse(["User-agent: *", "Disallow:"])
            elif response.status_code == 200:
                parser.parse(response.text.splitlines())
            else:
                log.warning("robots_policy_unavailable host=%s status=%s", urlsplit(origin).hostname, response.status_code)
                raise AccessRestricted("Could not establish robots policy; access denied")
            self.robots[origin] = parser
            log.debug("robots_policy_loaded host=%s status=%s", urlsplit(origin).hostname, response.status_code)
            return parser
        raise AccessRestricted("Too many robots redirects")

    def authorize(self, url):
        """Check both the declared host boundary and the source's robots policy."""
        origin = self._validate(url)
        parser = self.robots.get(origin) or self._load_robots(origin)
        if not parser.can_fetch(self.settings.user_agent, url):
            log.warning("request_blocked reason=robots_policy host=%s", urlsplit(url).hostname)
            raise AccessRestricted("robots.txt disallows this request")

    def get(self, url):
        """Reauthorize each redirect instead of trusting the initial destination."""
        for _ in range(6):
            self.authorize(url)
            response = self._raw(url)
            if response.is_redirect:
                if not response.headers.get("location"):
                    raise AccessRestricted("Invalid redirect")
                url = urljoin(url, response.headers["location"])
                continue
            if response.status_code in {401, 403}:
                log.warning("request_blocked reason=source_denied_access host=%s status=%s",
                            urlsplit(url).hostname, response.status_code)
                raise AccessRestricted("Source denied access; no bypass attempted")
            if response.is_error:
                log.error("http_request_failed host=%s status=%s", urlsplit(url).hostname, response.status_code)
            response.raise_for_status()
            return response
        raise AccessRestricted("Too many redirects")

    def get_json(self, url):
        return self.get(url).json()

    def get_soup(self, url):
        from bs4 import BeautifulSoup
        return BeautifulSoup(self.get(url).text, "html.parser")
