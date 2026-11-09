# Learning software engineering through JobScrap

This roadmap follows the existing implementation. Application code should change only for authorized development or an agreed learner exercise, not to rebuild the project for teaching. Teach concepts through behavior, boundaries, failure cases and trade-offs; basic language syntax is optional on request.

## Progress

| Module | Status | Evidence / next action |
| --- | --- | --- |
| 1. Architecture and execution flow | In progress | Initial walkthrough prepared on 2026-11-09; quiz and exercise below await the learner's attempt. |
| 2. Domain models and verified dates | Not started | Follow module 1 review. |
| 3. Persistence, identity and deduplication | Not started | Review code first; live database exercises require a disposable test database. |
| 4. Adapter contracts and scraping transport | Not started | No real company adapters are implemented. Use mocks for initial exercises. |
| 5. Resume processing and deterministic matching | Not started | Study the default free scoring path before optional AI. |
| 6. AI integration, caching and cost controls | Not started | Initial exercises use a fake backend and make no paid calls. |
| 7. Reliability, concurrency and recovery | Not started | Build on the orchestration and storage modules. |
| 8. Dashboard, HTTP and request security | Not started | Runtime-dependent exercises require installed web dependencies. |
| 9. Configuration, scheduling and deployment | Not started | Study provided units; no VM has been deployed. |
| 10. Testing, observability and integration review | Not started | Testing is practiced in every earlier module; this module connects the evidence. |

Statuses: **Not started → In progress → Awaiting review → Complete**, with **Needs revision** when an attempt reveals gaps. Completion requires a reviewed explanation and exercise demonstrating the concepts. Presenting a lesson or passing a test alone does not establish understanding. No learner answers have been assessed yet.

## Module map

| Module | Concepts to learn | Existing files and functions | Hands-on coding/debugging exercise |
| --- | --- | --- | --- |
| 1. Architecture and execution flow | Separation of responsibilities; orchestration; shared library vs separate processes; contracts; dependency injection; side effects | `src/jobscrap/cli.py:main`; `pipeline.py:execute`, `_score_job`; `adapters/base.py:Adapter`, `ScrapeBatch`; `tests/test_pipeline.py:PipelineTests` | Add an isolated orchestration test for an empty partial snapshot; compare it with an empty complete snapshot. Full assignment below. |
| 2. Domain models and verified dates | Business invariants; immutable value objects; timezone-aware UTC; provenance; rolling windows; conservative handling of incomplete evidence | `domain.py:PostingDate.from_iso`, `PostingDate.recent`, `JobListing`; `tests/test_domain.py` | Add a fixed-time date-only test using `Asia/Kolkata` near the rolling cutoff. Predict its UTC value and acceptance before running it; explain why discovery time cannot repair absent evidence. |
| 3. Persistence, identity and deduplication | Documents and collections; unique compound/partial indexes; upserts; idempotency; canonical URLs; identity vs content; compare-and-set | `storage.py:MongoRepository.ensure_indexes`, `_existing`, `upsert_job`, `set_match`, `recent_jobs`; `domain.py:canonical_url`, `JobListing.identity`, `JobListing.content_hash`; `tests/test_storage.py` | Use the mocked repository suite to test two different URL-only jobs from one company, then promote one to an ATS ID. Assert two records remain and the promoted record preserves its original `_id` and `first_seen_at`. |
| 4. Adapter contracts and scraping transport | Translating external data to a common model; plugin discovery; pagination; complete vs partial snapshots; HTTP failure semantics; robots policy; pacing; retries | `adapters/base.py`; `adapters/registry.py:discover_adapters`; `companies.py:validate_company`; `scraping/http.py:ScrapeHTTPClient`; `scraping/browser.py:rendered_page`; `tests/test_http.py` | With a mock transport and injected sleep/clock, test a 429 response followed by success. Verify `Retry-After` is respected without real sleeping or network access. No company integration is needed. |
| 5. Resume processing and deterministic matching | Text normalization; fingerprints; skill aliases and token boundaries; explainable heuristics; limitations of keyword coverage | `resume.py:Resume.from_text`, `load_resume`, `redact_contacts`; `matching.py:skills`, `terms`, `deterministic_match`; `tests/test_matching.py` | Construct two synthetic job descriptions with the same skills but different seniority requirements. Add a test demonstrating the heuristic's limitation and explain the result without pretending the score establishes eligibility. |
| 6. AI integration, caching and cost controls | Backend abstraction; validated output; untrusted inputs; cache key design; invalidation; prefilters; budgets; retryable fallbacks; logical vs physical requests | `matching.py:AIScorer`, `LangChainScorer`, `MatchingService.cache_key`, `MatchingService.score`, `scorer_signature`; `pipeline.py:_score_job`; `tests/test_matching.py` | Write a fake backend that fails once and succeeds on the next evaluation. Test that the first fallback stays retryable, the successful result is cached, and a subsequent call avoids the backend. Do not use real API keys. |
| 7. Reliability, concurrency and recovery | Bounded retries; crash recovery; file-lock scope; idempotent replay; failure boundaries; interruption history; partial success | `locking.py:run_lock`; `storage.py:retry_db`, `recover_interrupted_runs`; `pipeline.py:execute`; `tests/test_pipeline.py`; `tests/test_config_companies_locking.py` | Add a two-company orchestration test: the first adapter fails, the second succeeds. Assert the second runs, the parent reports partial success, and missing jobs are never closed for the failed company. |
| 8. Dashboard, HTTP and request security | App lifecycle; read vs write requests; server-side templates; thread workers; pagination; stale-score presentation; authentication; CSRF; readiness | `web/app.py:create_app`, nested `lifespan`, `dashboard`, `action`, `work`, `health`; `web/templates/dashboard.html`; `web/static/dashboard.css`; `tests/test_web.py` | Add a request test with a valid form token but a foreign Origin. Verify the action is rejected and the injected runner is never called. Explain why a UI message is not evidence of persisted job data. |
| 9. Configuration, scheduling and deployment | Environment-based configuration; venv and packaging; relative paths; independent process lifecycles; service identities; systemd timers; restart policy; least privilege | `config.py:Settings`, `Settings.from_env`; `cli.py:main`; `.env.example`; `pyproject.toml`; `deploy/*.service`, `deploy/*.timer`; `docs/DEPLOYMENT.md` | Write an isolated configuration test using a temporary `.env` and an unrelated working directory. Verify relative resume/state paths resolve against that `.env` and shell variables retain precedence. Do not modify real credentials or install services. |
| 10. Testing, observability and integration review | Unit vs mocked boundary vs live integration tests; behavioral assertions; false confidence; safe logs; metrics/counters; evidence-based claims | `tests/test_domain.py`, `test_matching.py`, `test_pipeline.py`, `test_storage.py`, `test_http.py`, `test_web.py`, `test_observability.py`; `observability.py:configure_logging`, `log_context`, `log_failure`; `pipeline.py:execute` | In a disposable copy, deliberately remove the complete-snapshot guard and show the appropriate regression test fails. Restore the copy, then explain which remaining live integrations that test does not verify. |

For every module: introduce the relevant data flow, inspect a short real code path, discuss the chosen design and a reasonable alternative, then assign 3–4 reasoning questions and the small exercise. Quiz answers and exercise solutions are intentionally not supplied in advance. Future module assignments can be adjusted to the learner's demonstrated knowledge and project state.

## Module 1 — Architecture and execution flow

### Goal

Explain how a job moves from a source to storage, scoring and display, identify who owns each decision, and test an orchestration rule without an external service.

### Reading order

1. `pyproject.toml`: the `jobscrap` executable maps to `jobscrap.cli:main`.
2. `src/jobscrap/cli.py:main`: constructs configuration and the repository, dispatches commands and closes resources. The `serve` branch constructs the web app instead of calling the scrape pipeline.
3. `src/jobscrap/pipeline.py:execute`: coordinates a scrape or rescore under the shared file lock. Read the two branches separately.
4. `src/jobscrap/adapters/base.py`: a source returns `ScrapeBatch` containing common `JobListing` values; the complete-snapshot flag controls whether absence is evidence of closure.
5. `tests/test_pipeline.py:PipelineTests.setUp` and `run_pipeline`: injected repository, adapter, resume loader and client keep the orchestration tests independent of real MongoDB, websites, PDFs and AI APIs.

### Actual paths through the code

```text
Daily trigger:
jobscrap-daily.timer -> jobscrap-daily.service -> jobscrap scrape

Scrape:
cli.main -> Settings.from_env + storage.connect -> pipeline.execute
  -> run_lock -> database checks/recover unfinished history -> enabled companies
  -> load_resume -> MatchingService
  -> discover/select adapter -> adapter.fetch using the HTTP client
  -> ScrapeBatch[JobListing + PostingDate evidence]
  -> validate/deduplicate batch -> check each date -> repository.upsert_job
  -> _score_job -> existing valid match OR MatchingService.score/cache/backend
  -> repository.set_match -> optional complete-snapshot closure -> finish history

Rescore:
cli.main -> pipeline.execute(kind="rescore")
  -> stored recent active jobs -> _score_job; no source fetch

Dashboard read:
cli.main(serve) -> create_app -> GET / -> repository.recent_jobs
  -> current resume/scorer/content checks -> Jinja template -> HTML

Dashboard manual action:
POST /actions/scrape or /actions/rescore -> validate request -> executor worker
  -> work -> pipeline.execute -> same storage/matching rules
```

`execute` is the coordinator: it selects and sequences operations. Adapters know source formats; domain objects describe jobs and enforce local invariants; the repository owns database operations; the matcher computes/reuses scores. MongoDB jobs use the `jobs` collection, completed reusable assessments use `match_cache`, runs use `scraping_history`, configuration uses `companies`, and lightweight shared metadata uses `metadata`.

The architecture is one modular Python application used by a short-lived command process and a long-lived web process. The timer launches the command independently. Module boundaries and operating-system process boundaries are different things. They share MongoDB and, on the supported single host, the configured lock file; they do not share Python in-memory state across processes.

### Decisions and alternatives

- **Shared pipeline:** command-line, scheduled and dashboard-triggered runs reuse the same rules. Copying the workflow into each entry point would let date/deduplication behavior drift.
- **Source adapters:** each source translates its format to `JobListing`; the pipeline stays reusable. A source-specific conditional inside `execute` would couple orchestration to every integration.
- **Repository object:** callers use operations such as `upsert_job` rather than constructing MongoDB updates throughout the app. Direct database access could reduce indirection in a tiny script but would spread storage rules across multiple entry points here.
- **Injected collaborators:** `execute(repository, ..., resume_loader=..., client_factory=...)` receives collaborators that tests can replace. Constructing every collaborator inside `execute` would make isolated tests harder. The current repository dependency uses matching method names rather than a formal repository interface, so some interface errors are detected at runtime.
- **Domain validation and I/O:** `PostingDate.recent(now)` is a local deterministic decision. Fetching a website, writing a document, calling an external model and acquiring a file lock are side effects. Keeping these distinctions visible makes testing cheaper and failure analysis clearer.
- **Separate scheduling:** a timer launches the same CLI even while the dashboard is stopped. An in-process web scheduler would tie daily work to web-server lifetime and worker configuration.

These boundaries are pragmatic: `execute` still coordinates many responsibilities, the matcher uses the repository for its cache, and stored matches are dictionaries rather than a single shared domain type. Those are useful trade-offs to evaluate during future work, not reasons for an unrequested refactor now.

### Quiz — submit your own reasoning

1. If the dashboard stops overnight, what can still trigger a daily scrape? Name the relevant files and distinguish the shared code from the running processes.
2. A listing was posted four days ago but first discovered today. Trace where it is rejected. Why must dashboard reads also check freshness after a job was previously stored?
3. Where should a newly inspected ATS's field mapping belong? What does its adapter return, and which core component should remain unaware of that ATS's JSON field names?
4. Why does `PipelineTests.run_pipeline()` supply a repository, resume loader and client factory? What can a passing test using those replacements prove, and what live behavior can it not prove?

### Your exercise — empty snapshots

Add test case(s) **only in `tests/test_pipeline.py`**, using its existing helpers. Leave application code unchanged.

- An adapter returns an empty **partial** snapshot. Verify the pipeline actually calls the adapter and finishes successfully, does not upsert jobs, and never calls `mark_missing`.
- Compare with an empty **complete** snapshot: verify that the pipeline asks the repository to reconcile missing jobs for the correct company with an empty seen-ID collection.
- Explain the production failure that treating every empty response as complete could cause.

Verify successful execution as well as absence of destructive calls: an early unrelated failure should not make a safety test pass accidentally. These tests check whether orchestration requests closure; the mocked repository does not prove MongoDB actually changes records correctly.

Run from the project root:

```sh
PYTHONPATH=src .venv/bin/python -m unittest tests.test_pipeline -v
```

This targeted suite uses standard-library replacements, so the documented missing runtime dependencies should not block this exercise. If it fails to start, share the error rather than installing or changing unrelated components just to make it pass.

Submit quiz answers, your test diff or test code, and the command output. Review will assess component ownership, causal reasoning, meaningful assertions, isolation, coverage of both snapshot cases and awareness of what mocks cannot verify. No exercise solution has been implemented or reviewed.

## Learning review log

- **2026-11-09:** Existing implementation inspected; modules mapped; first architecture lesson and assignment prepared. Module 1 remains in progress pending the learner's answers and exercise. Application code and implementation-focused project memory are unchanged.
- **Instructor verification, 2026-11-09:** Re-ran the full existing suite: 73 discovered, 47 passed, 26 explicitly skipped for missing dependencies/live Atlas configuration. Source/test compile checks passed. This is verification of the existing baseline, not a learner assessment or live integration verification.
- **Comment/logging support, 2026-11-09:** At the learner's request, added explanatory docstrings/comments and shared contextual logging. For the next walkthrough, follow a run ID through `pipeline.execute`, inspect `observability.log_context` for inherited identifiers, and compare INFO lifecycle events with DEBUG decisions. No quiz answers or assigned empty-snapshot tests were supplied; module 1 remains in progress. This implementation work does not count as demonstrated learner understanding.

Verification baseline is the implementation memory's recorded 47 passed / 26 skipped, not evidence that external services work. Record any newly run checks accurately without conflating instructor checks with learner progress. Do not put answers, grades or lesson notes in `PROJECT_MEMORY.md`.
