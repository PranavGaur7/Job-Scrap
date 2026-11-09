# Adapter reliability and verification

Use these rules when assessing a source; implement and test them when adapter work is requested.

## Identity and deduplication

Prefer a stable source job/requisition ID scoped by company/tenant/provider as needed. Deduplicate overlapping pages and repeated runs through the existing persistence mechanism. Preserve distinct requisitions with identical titles. Model multiple locations without duplicating a requisition unless the project explicitly models job-location pairs.

When no ID exists, use a canonical detail URL with only verified tracking parameters removed. Document any fallback and its collision risks; title alone is not an identity. A retry or partial run must not create duplicate jobs.

## Verified publication dates

Retain the original value and provenance and parse using explicit timezone/precision rules. Do not relabel updatedAt, first-seen time, collection time, a closing date, or a timestamp guessed from an ID as a posting date. A field name is a candidate, not proof of its semantics.

Use the requested date interval and the project's boundary convention; if unspecified, document the chosen boundaries and timezone. Include only records whose verified publication dates satisfy the filter. Keep missing, malformed, ambiguous, or unverified dates classified separately and report excluded/unknown counts. Never silently treat them as recent. For date-only or relative values, preserve precision and use an interval or conservative policy when the cutoff overlaps the uncertainty.

If the source has no verified posting date, report that limitation and offer a separately labeled first-seen tracking strategy. Do not promise historical date filtering that the source cannot support.

## Requests and failure handling

Reuse configured timeouts and rate limits. Without project defaults, start with concurrency one and at least one second between requests per host; adapt downward to server signals. Retry transient network failures, 429, and retryable 5xx responses with bounded exponential backoff and jitter (at most three retries by default), honoring Retry-After. If the server delay exceeds the run budget, defer or stop rather than retry early.

Do not repeatedly retry permanent 4xx responses or bypass authentication/challenges. Stop on access barriers and describe the required authorized next step. Validate content type and payload shape, and distinguish a genuine empty board from an error page, missing job array, or schema change.

Bound page count and total runtime, detect repeated cursor/page content, and terminate on the observed source end condition. Expose incomplete runs with useful context and counts; never report partial output as a successful complete crawl or expire unseen stored jobs after a failed run. Avoid logging credentials or session material.

## Fixture-based tests

Use the project's test runner and sanitized representative source payloads/HTML with provenance notes. Mock transport and time so normal tests are deterministic and offline. Store only the fixtures needed to verify behavior.

Cover the behaviors relevant to the adapter:

- Field mapping, stable identity, canonical URLs, and multi-location records.
- Multiple pages, overlap deduplication, empty final page or missing next cursor, and repeated cursor/page termination.
- Same-run and repeated-run deduplication, plus different IDs with equal titles.
- Verified date parsing, timezone/cutoff boundaries, missing/malformed dates, and modified-only records excluded from publication-date filtering.
- Timeouts, 429/Retry-After, retryable 5xx, bounded retries, permanent errors, unexpected schemas, and partial-run signaling.

Assert outcomes and meaningful invariants, not incidental implementation text. Verify pacing and retries with a fake clock or mocked sleeper. Run relevant adapter and shared-contract tests; report commands, results, and any live behavior that remains unverified.
