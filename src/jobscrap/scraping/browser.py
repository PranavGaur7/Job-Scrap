"""Opt-in standalone Python Playwright helper for future JavaScript adapters."""
from contextlib import contextmanager

from .http import AccessRestricted


@contextmanager
def rendered_page(url, client):
    from playwright.sync_api import sync_playwright

    client.authorize(url)
    with sync_playwright() as playwright:
        # Linux headless Chromium needs OS libraries, not an X server. Keep sandbox enabled.
        browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
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
        finally:
            browser.close()
