# Architecture and storage

```text
Company config -> adapter registry -> standalone HTTP / optional Playwright
                  -> JobListing + PostingDate evidence
                  -> UTC 72h validation -> Mongo upsert + content fingerprint
                  -> deterministic match -> optional budgeted LangChain -> score cache

systemd timer / CLI / dashboard manual action -> same pipeline + local file lock
FastAPI dashboard -> Mongo queries with rolling date filter -> escaped Jinja HTML
```

The CLI and dashboard share library code, not an embedded scheduler. The dashboard is synchronous for PyMongo reads, which FastAPI runs in its worker pool; manual pipelines use one separate executor worker. The scheduler is an independent Python CLI process. Adapters own company/ATS specifics and pagination, while the core owns dates, identities, storage and scoring. No source-specific switch statements are needed in the pipeline.

## Collections

| Collection | Identity and purpose | Important fields/indexes |
| --- | --- | --- |
| `companies` | `_id`: stable user-chosen company slug | `name`, `careers_url`, `adapter`, `adapter_config`, `enabled`, `integration_status`, created/updated times |
| `jobs` | `_id`: hash of company + initial stable ID or canonical URL; preserved during URL-to-ID promotion | Unique partial `(company_id, external_job_id)` and unique `(company_id, canonical_url)`; verified/active/posting-date index |
| `scraping_history` | `_id`: UUID per overall/company run | `kind`, `company_id`, `parent_run_id`, `status`, started/finished times, safe error class, counters; time/company indexes |
| `match_cache` | `_id`: SHA-256 of resume hash + meaningful job-content hash + scorer signature | validated score/explanation, matched/missing skills, method/provider configuration hash, `scored_at`; `_id` provides uniqueness |
| `metadata` | `_id`: metadata name, currently `resume` | resume content hash and update timestamp; no raw resume text |

Job documents contain title, company ID/name, location, plain-text description, application/canonical URLs, optional ATS ID, `posted_at`, `posting_evidence`, `first_seen_at`, `last_seen_at`, `date_verified`, `active`, `content_hash`, `previous_content_hash`, `content_changed_at` and optional `match`. `posting_evidence` stores raw date, source URL/field, semantic kind, precision and UTC timestamp. There is no substitution of discovery time for posting time.

MongoDB dates are UTC and the driver returns aware datetimes. The unique external-ID index is partial because fallback records have no external ID. URL normalization deliberately preserves potentially meaningful parameters. All writes that may be retried use upserts or idempotent sets. Scoring persistence compares the current content hash before writing. Duplicate-key races are re-read and reconciled once; inconsistent identities fail explicitly. File locking serializes the supported single-host pipeline. Database-level indexes also protect against accidental duplicate imports/writers.

The database wrapper combines PyMongo's retryable reads/writes with two application retries for transient failures; connect, socket, server-selection and operation timeouts are bounded. Errors expose safe classes to logs/history and generic text to the UI. Authentication/configuration failures are not treated as unlimited transient retries. Completed work persists when a later company fails. Scraping history is best-effort when the database itself is unavailable; systemd's journal still records errors.

No automatic TTL deletes are configured. Expired jobs are hidden by reads while preserved for identity history and inspection. History/cache retention should be chosen after measuring real usage. Do not use a TTL index on `posted_at` without considering reappearance, cache reuse and audit needs. There is no dashboard job editing, arbitrary SQL/query interface, public user registration or application submission.

## Libraries and decisions

- PyMongo, not an ODM: direct unique indexes and predictable upserts. TLS certificates and hostnames stay verified.
- Plain dataclasses for domain objects; source evidence and validation have no web/database dependency.
- HTTPX/BeautifulSoup first. Playwright is an optional extra, isolated to source adapters requiring a browser.
- LangChain is optional at install time and lazy at runtime. A minimal structured scoring call is sufficient; autonomous agents and embeddings would add cost and infrastructure before real sources justify them.
- A file lock is sufficient for the explicitly requested single VM and works on macOS/Linux. Use the same `STATE_DIR` and account for CLI, dashboard and timer. Multiple machines would need distributed coordination before deployment.
- Jinja and CSS avoid a separate JavaScript build. Basic auth plus loopback/SSH is suitable for a personal MVP; internet-facing authentication must remain behind HTTPS.

The project has version bounds, not a fabricated resolved lockfile: dependencies could not be downloaded in the initial environment. After a successful install and full tests on the target VM, record its resolved environment with `python -m pip freeze > requirements.lock.txt`, review it for local editable paths, and retain it privately or commit a sanitized portable lock for repeatable deployments.

Official references used: [PyMongo indexes](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/indexes/), [PyMongo TLS](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/security/tls/), [LangChain models and structured output](https://docs.langchain.com/oss/python/langchain/models), [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [FastAPI templates](https://fastapi.tiangolo.com/advanced/templates/).
