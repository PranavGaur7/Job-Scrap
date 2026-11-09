"""Opt-in standalone Python Playwright helper for future JavaScript adapters."""
from contextlib import contextmanager
import logging

from .http import AccessRestricted
from ..observability import log_failure

log = logging.getLogger(__name__)


@contextmanager
def rendered_page(url, client):
    """Yield a live page for source-specific extraction, then close browser resources.

    Browser requests still pass declared-host and robots checks. The adapter owns
    extraction and must wait for its actual source data rather than a fixed sleep.
    """
    from playwright.sync_api import sync_playwright

    client.authorize(url)
    with sync_playwright() as playwright:
        # Linux headless Chromium needs OS libraries, not an X server. Keep sandbox enabled.
        browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
        log.debug("browser_started headless=true sandbox=true")
        try:
            context = browser.new_context(user_agent=client.settings.user_agent, service_workers="block")

            def route_request(route):
                try:
                    client.authorize(route.request.url)
                    client.throttle(route.request.url)
                except (AccessRestricted, ValueError):
                    route.abort("blockedbyclient")
                    return
                route.continue_()

            context.route("**/*", route_request)
            page = context.new_page()
            page.set_default_timeout(client.settings.http_timeout * 1000)
            response = page.goto(url, wait_until="domcontentloaded")
            if response is None or response.status >= 400:
                raise AccessRestricted("Browser navigation failed or source denied access")
            yield page
        except Exception as exc:
            log_failure(log, "browser_extraction_failed", exc)
            raise
        finally:
            browser.close()
            log.debug("browser_closed")
