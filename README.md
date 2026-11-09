# JobScrap

A small Python system for verified recent jobs, resume matching and one dashboard. Scraping and scoring run as a CLI process; the dashboard is a separate FastAPI process. MongoDB Atlas is the only database. No Redis, embedded scheduler, Node build, or production MCP dependency.

**Implemented core:** company registration/import, adapter discovery, standalone HTTP transport and optional headless Playwright helper, posting-date evidence and a rolling 72-hour filter, MongoDB indexes/upserts/change tracking, PDF resume loading, deterministic matching, optional provider-swappable LangChain matching, persistent score caching, manual runs, history, process locking, dashboard and systemd units.

**Awaiting integration:** actual company adapters and live source verification. No company was supplied, no real source has been scraped, and no job data is seeded. Registering a company without an adapter creates a disabled `awaiting_adapter` record. Atlas/OpenAI need your credentials. Jev is not a dependency: the core decisions are deterministic, and no particular Jev package or service was identified in the request.

## Local setup

Use Python 3.11 or later; Python 3.12/3.13 is a conservative VM choice. Run these commands from this directory:

On the inspected Mac, the default `python3` is 3.9.6; use `/opt/homebrew/bin/python3` (3.15.2) to create the venv instead. A venv was created there during implementation, but dependencies could not be installed. Check your selected interpreter before running setup.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[test]'
cp .env.example .env
chmod 600 .env
```

Edit `.env` with your Atlas connection string. The supplied `Pranav_Gaur_Resume.pdf` is the default resume. Its filename is configurable; it is ignored by Git along with `.env` and runtime files. Keep a private backup and copy it separately to the VM. Relative resume/state paths resolve against the selected `.env` directory, not a hardcoded home directory. Use `JOBSCRAP_ENV_FILE=/absolute/path/.env` when running elsewhere.

```sh
jobscrap resume-info
jobscrap doctor
jobscrap init-db
jobscrap adapters
jobscrap serve
```

Open **http://127.0.0.1:8000**. The dashboard also opens with a configuration notice before Atlas is configured; it contains no sample jobs. `doctor` checks the PDF and database without paid calls. `init-db` creates indexes and must run before normal use. `/healthz` returns 503 when the database is unavailable; it is a database readiness check, not a scraper/source health guarantee.

## Companies and manual runs

Register a provided company using a stable company ID:

```sh
jobscrap companies add --id company-slug --name 'Company Name' --url 'https://careers.example.com'
jobscrap companies list
```

The URL above is a placeholder. Without `--adapter`, registration is disabled and does not scrape. Names alone can be registered after their official careers URL is identified; this MVP does not guess career domains. To import a list, create a JSON file with this structure, replacing placeholders:

```json
[
  {
    "id": "company-slug",
    "name": "Company Name",
    "careers_url": "https://careers.example.com",
    "adapter": null,
    "adapter_config": {},
    "enabled": false
  }
]
```

```sh
jobscrap companies import companies.json
jobscrap scrape
jobscrap scrape --company company-slug
jobscrap rescore
jobscrap rescore --company company-slug
jobscrap rescore --force
```

After a verified adapter is implemented, register again with its name using `--adapter adapter-name --config adapter-config.json`; this upserts the company without duplicating it. Batch imports validate all entries before writing, but are not multi-document transactions; rerunning after a database outage is safe. Use `--disabled` to pause a registered company. Disabled companies are not scraped; their already stored jobs still expire normally and can be rescored.

Dashboard **Run scrape** and **Rescore** invoke the same pipeline in one bounded background worker. Refresh to view activity. CLI, dashboard and timer share a nonblocking file lock in `STATE_DIR`, so they do not make concurrent paid requests. A crashed process releases the lock automatically; the next run marks unfinished history as `interrupted`. All processes must use the same state directory on the same VM. Multiple hosts are outside this MVP's coordination model.

## Date and identity rules

- Only source-verified posting dates within `[now − 72 hours, now]` are inserted. Read queries repeat the filter, so jobs disappear as they age even if the timer is stopped. Future, missing, relative and ambiguous dates are excluded. `updated_at`, `dateModified` and discovery dates cannot establish posting dates.
- Every accepted date stores the raw value, semantic field name, source URL, evidence type and precision. An adapter must prove these semantics against its real source; the core cannot independently authenticate arbitrary adapter claims.
- Naive/date-only values require an established source timezone. Date-only values use that local day's start, conservatively excluding days straddling the 72-hour cutoff. This can omit genuinely recent boundary-day jobs. Ambiguous/nonexistent DST wall times are rejected.
- `(company_id, external_job_id)` has a unique partial index for nonempty ATS IDs. `(company_id, canonical_url)` is also unique. URL normalization removes fragments and recognized tracking parameters, while preserving functional query parameters, path case and trailing slashes.
- A URL-only record can gain an ATS ID without duplication. A reused URL with conflicting IDs raises an identity conflict for adapter investigation. Company IDs must stay stable. `first_seen_at`, `last_seen_at` and `posted_at` are separate fields.
- Content changes record `previous_content_hash` and `content_changed_at` and invalidate the previous match. This is lightweight change tracking, not a complete version archive. Unchanged sightings preserve the score and original discovery time.
- Missing/unverifiable date evidence on a later sighting hides the existing record. Only a fully successful, explicitly complete snapshot may mark disappeared jobs inactive. An adapter that limits by date or has partial pagination must use `complete_snapshot=False`.

## Matching and cost controls

The default skills heuristic recognizes technical skills and aliases and combines 80% recognized skill coverage with 20% keyword coverage. It is deterministic, explainable and free. It does not establish years of experience, location eligibility, mandatory qualifications or hiring probability. Matched/missing skills and the scoring method are visible in job details.

To opt into AI:

```sh
python -m pip install -e '.[ai,test]'
```

Set `MATCHING_MODE=hybrid`, `AI_PROVIDER=openai`, an `AI_MODEL` available to your account with structured-output support, and `OPENAI_API_KEY` in `.env`. No paid model is silently selected. LangChain's `init_chat_model` and a validated schema wrap the provider. Other providers require their LangChain integration package and environment keys, plus a structured-output capable model; they have not been live-tested here.

Low baseline matches skip AI (`AI_PREFILTER_MIN_SCORE`). Cache keys include normalized resume text, job title/location/description, scorer/prompt version and provider/model/prefilter configuration. URL/date/discovery changes alone do not trigger a new AI call. New or meaningfully changed records, resume updates and changed scorer settings are automatically rescored on the next run; `rescore` also handles stored active recent jobs without scraping.

`AI_MAX_CALLS_PER_RUN` bounds attempted logical AI evaluations, including initialization failures. Each evaluation permits one SDK retry, so physical provider requests can be up to twice this limit. Input/output sizes are bounded. Deferred or failed AI assessments keep a clearly labeled deterministic score and remain retryable; they are not cached as completed AI results. Cached completed results are reused. `rescore --force` deliberately bypasses the cache, still within the call budget. A successful cache write followed by a failed job update is recoverable; a crash between a provider response and its cache write can cause a repeated charge on rerun. There is no exactly-once billing guarantee.

Budget deferral produces `partial` history but CLI exit 0, avoiding automatic paid retry loops. Source/database/AI failures return nonzero and systemd may retry the run. Thus the systemd retry policy can multiply the per-run spend; set the cap and provider project budget accordingly. Completed cached work is reused across retries. Contact details are redacted before AI calls, but the remaining resume and job content are sent to the configured provider when hybrid mode is enabled. Raw resume text is not stored in MongoDB or logs.

The dashboard suppresses stale scores when the resume, scorer settings or job content changes. Sorting by match score shows currently scored records; default newest-first also shows pending records. Pagination displays 25 jobs at a time. All displayed times are UTC. The dashboard uses Basic auth when configured, form tokens and origin checks for manual actions; keep it on loopback/SSH or behind HTTPS.

## Comments and logging

Start with the module docstrings in `pipeline.py`, `domain.py`, `storage.py` and `matching.py`. Function docstrings explain contracts, and inline comments explain decisions such as complete-snapshot closure, identity versus content hashes, and retryable AI fallbacks. They describe the implemented behavior without treating mocked tests as proof of live integrations.

`src/jobscrap/observability.py` configures Python's standard logging. Application events have a UTC timestamp, severity, module, run/company/job identifiers where available, and a stable event name. Context follows the shared pipeline across module calls and is restored after each company/job scope. A dash means that identifier does not apply to the event.

| Level | What to look for |
| --- | --- |
| `INFO` | Commands, dashboard lifecycle, run/company progress, source counts, completion duration and counters |
| `DEBUG` | Accepted/excluded identities, duplicate counts, cache decisions, rescore pages, HTTP status/body size/timing, and safe failure locations |
| `WARNING` | Retries/backoff, blocked access, a busy pipeline, partial results, and an exhausted AI budget |
| `ERROR` | Failed database operations, source/company failures, failed manual runs and command failures |

Set `LOG_LEVEL=DEBUG` in `.env` for detailed investigation; restart the dashboard to reload its configuration. An individual CLI command can override it with `LOG_LEVEL=DEBUG jobscrap scrape`. Application logs exclude raw exception messages, resume/job text, credentials and full request URLs. Safe failure locations contain file basenames, line numbers and function names without source lines or local variable values. Do not add sensitive values to log messages or context. Third-party debug request tracing is suppressed, and Uvicorn access logs are disabled so query strings are not recorded by that access logger.

Diagnostics go to **stderr**, while CLI JSON/results stay on **stdout**. On the VM, systemd captures stderr in journald and manages retention. Locally, read logs in the terminal or capture them separately:

```sh
mkdir -p var
jobscrap scrape > var/result.json 2> var/run.log
```

That local command overwrites the two files on each invocation; use a fresh filename to keep previous captures. The application intentionally does not rotate one shared file from multiple processes. Use `journalctl -u jobscrap-daily.service --since today` for persistent VM history.

## Tests and validation

```sh
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

Core tests use only Python's standard library. Runtime-dependent suites skip with explicit reasons when dependencies are missing. Repository tests use mongomock to check indexes/upserts, URL-to-ID promotion, first/last-seen handling, stale-score rejection, expiry, history, and retries. These are not proof of Atlas connectivity or real server index behavior. HTTP tests use `httpx.MockTransport`; dashboard tests use FastAPI's `TestClient`. All job fixtures are synthetic and confined to tests; no fixture is loaded into the app.

To run the real Atlas integration test, set **`MONGODB_TEST_URI`** in your shell using a dedicated test account. It creates and drops a random `jobscrap_test_<uuid>` database. This test is opt-in and requires permissions for that disposable database. It does not use production job data or the `.env` production URI automatically.

During initial implementation, package downloads were blocked by this execution environment's DNS/network restrictions. Core standard-library tests ran locally; dependency-backed tests, real Atlas connectivity, live OpenAI calls, dashboard browser rendering and Linux headless Chromium still need the checks above after installation. No live scraper source has been tested.

See [Linux deployment](docs/DEPLOYMENT.md), [database/architecture](docs/ARCHITECTURE.md) and [adding adapters](docs/ADAPTERS.md). [PROJECT_MEMORY.md](PROJECT_MEMORY.md) records progress and limitations; [AGENTS.md](AGENTS.md) contains permanent development instructions.
# Job-Scrap
