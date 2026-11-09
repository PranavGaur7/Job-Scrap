# Adding a company integration

No company adapter ships in this MVP. Adding a URL registers a source for inspection; it does not make generic scraping reliable.

When a company is provided, read `PROJECT_MEMORY.md` and the project `company-career-scraper` skill. Inspect its official careers page with development Playwright MCP, identify the ATS and real data requests, check access restrictions, and establish posting-date semantics. Prefer official/public ATS APIs, then structured `JobPosting` data, then HTTP/BeautifulSoup. Use standalone Python Playwright only when required. Development tools must not be imported by production code.

## Adapter contract

Add `src/jobscrap/adapters/<source_name>.py` exporting an `ADAPTER` instance with:

```python
class CompanyAdapter:
    name = "verified-source-name"

    def validate_config(self, company: dict) -> None:
        # Validate the specific tenant/API settings established during inspection.
        ...

    def allowed_hosts(self, company: dict) -> set[str]:
        # Exact known API/careers/CDN hostnames, not wildcard arbitrary origins.
        ...

    def fetch(self, company: dict, client) -> ScrapeBatch:
        # Retrieve all required pages using the injected ScrapeHTTPClient.
        # Return validated JobListing objects; attach PostingDate only when proven.
        ...

ADAPTER = CompanyAdapter()
```

This is a contract sketch, not a working scraper. The registry discovers modules automatically. Separately installed adapter packages may expose `jobscrap.adapters` entry points whose values are zero-argument adapter classes. Plugins are trusted local Python code, not user-supplied remote modules. Neither path requires changing pipeline or database logic.

`ScrapeHTTPClient` provides `get_json`, `get_soup` and `get`, with robots checks, pacing, explicit allowed hosts, bounded sizes/timeouts/retries and safe redirects. Request APIs must also be explicitly allowed by their robots policy. Robots lookup errors fail closed except a missing robots file. Source terms or other restrictions discovered during inspection remain the adapter developer's responsibility. Never solve CAPTCHAs, rotate proxies to bypass restrictions, access private endpoints, or reinterpret `updated_at` as a posting date.

Construct `PostingDate.from_iso(raw, source_url=..., field_name=..., kind=..., source_timezone=...)` only after establishing what the field means. `DateKind` supports `ATS_API`, `STRUCTURED_DATA`, and `VISIBLE_POSTED_DATE`. Convert documented numeric ATS epochs explicitly in the adapter with a proven unit/timezone; the generic parser deliberately does not guess epochs. A source with no trustworthy posting date should return listings with `posting_date=None` (excluded), or be documented as unsupported. Do not fill dates from `now`, scrape time, search snippets or update fields.

Return `ScrapeBatch(jobs, complete_snapshot=True)` only when every page succeeded and the result is a complete active-job snapshot. Leave it false for incremental/date-limited feeds. Raise on truncated/failed pagination or malformed source payloads; do not turn a failed fetch into an empty complete snapshot. Do not set it true if parsing silently skipped identifiable source records. An empty, *verified complete* source may legitimately close all prior jobs. Materialize the fetched jobs before returning; do not leave lazy network work outside the injected client's context.

Stable IDs belong to a single company. Prefer a job-specific canonical application URL. If the ATS uses a shared generic application form, establish a unique public job-detail URL as identity and add an explicit separate application-link policy before integration; the current unique URL index intentionally rejects generic links reused for different IDs.

## Required validation for a real adapter

1. Save sanitized real source fixtures containing known source IDs and actual posting fields; do not commit contacts, credentials or private data.
2. Test pagination, duplicated IDs, missing/future/old dates, timezone conversion, errors/timeouts and a changed description. Confirm cutoff filtering and ID/URL stability.
3. Verify a real source request outside MCP using the installed Python code. Compare several returned dates and links against the official site. Check robots, pagination totals, location/description normalization and rate limits.
4. Register the company with the adapter/config, run `jobscrap scrape --company <id>`, inspect history and Atlas documents, rerun unchanged and confirm no duplicate records or unnecessary AI calls.
5. Update `PROJECT_MEMORY.md` with the extraction strategy, evidence, fixture/live tests, date semantics and remaining limitations. Clearly separate live verification from mocked tests.

For JavaScript-only sources, install the browser extra and Linux Chromium dependencies as described in [deployment](DEPLOYMENT.md). `jobscrap.scraping.browser.rendered_page` launches headless Chromium with its sandbox enabled, routes requests through declared-host/robots/pacing checks, and blocks service workers. Wait for source-specific elements/API responses rather than assuming a fixed sleep is sufficient. Add an adapter-level bounded retry around idempotent browser fetches when necessary. Browser resource policies, Linux compatibility and extraction are not proven until the actual source is tested.
