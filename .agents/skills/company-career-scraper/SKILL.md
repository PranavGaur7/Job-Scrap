---
name: company-career-scraper
description: Inspect company career URLs with Playwright MCP, identify ATS and job data sources, and recommend reliable extraction. Implement company adapters only when requested.
---

# Company Career Scraper

Given a company career URL, investigate its job data and recommend the simplest reliable extraction method. A URL alone authorizes investigation, not adapter implementation.

## Project context

Read applicable AGENTS.md files and PROJECT_MEMORY.md first. Inspect existing adapters, job models, HTTP clients, configuration, deduplication, date filters, storage, and tests before proposing integration. Follow the actual architecture and reuse shared components. If none exists, state that fact; do not invent an established interface or introduce a framework during investigation.

## Investigate with Playwright MCP

Discover available Playwright MCP tools and use them to inspect the supplied page, redirects, embedded boards or frames, rendered jobs, and network requests. Observe an initial load, one pagination or load-more action when available, and a representative job detail. Inspect response bodies through available tools; do not claim network or field verification from a screenshot alone.

If Playwright MCP is unavailable, disclose that inspection is blocked. Use available read-only sources for clearly labeled preliminary findings, but do not silently substitute another browser or claim a completed Playwright inspection.

Identify with evidence:

- ATS/provider and tenant or board identifier; distinguish the company wrapper from its underlying jobs service.
- Endpoint, HTTP method, query/body parameters, response job path, required public headers, and whether a direct request works independently of browser session state.
- Pagination type, page size, totals or next cursor, and end condition. Verify a subsequent page when possible; record untested behavior explicitly. Guard against repeated pages/cursors and impose a finite page budget.
- Stable job or requisition ID, canonical detail URL, and whether locations or multiple boards duplicate the same job.
- Posting-date candidates in list data, details, embedded state, or JobPosting JSON-LD, with exact field paths, example values, timezone/precision, and evidence of meaning. Distinguish publication, modification, first-seen, and closing dates.

Use small, paced samples. Do not crawl the full board merely to identify its mechanism. Do not store cookies, tokens, or personal application data in notes or fixtures.

## Recommend extraction

Prefer, in order, a supported public ATS/API or feed, structured responses observed in browser traffic, embedded structured data, then HTML extraction. Use browser automation for ongoing extraction only when simpler sources cannot supply required fields reliably. Evaluate completeness, stable IDs, verified publication dates, pagination, and session requirements; an undocumented API may require more maintenance than structured page data.

Report the chosen method and why, observed endpoint/parameters and field mapping, pagination/end behavior, deduplication key, date confidence and filtering policy, limitations, and the smallest integration change needed. Separate observed facts from inference and unverified assumptions. Recommend a method even if no implementation was requested.

Apply the reliability rules in [references/adapter-reliability.md](references/adapter-reliability.md) to the recommendation; read its implementation and test details when an adapter is requested.

## Implement only when requested

Use the existing adapter contract, models, shared clients, configuration, and test conventions. Keep changes company-specific unless a shared fix is necessary for the requested integration. If architecture is still absent, choose a minimal implementation appropriate to the request and document the new conventions. Implement the reliability and fixture-test requirements in the linked reference and run relevant tests.

## Maintain project memory

After investigation or implementation, update PROJECT_MEMORY.md with the company and supplied URL, inspection date, evidence and source field paths, ATS/API findings, pagination, identity/date semantics, recommendation, unresolved limits, and implementation/test status. Preserve unrelated notes and distinguish inspection time from job publication time. Record only verified project conventions; never mark inferred behavior as tested. Creating this skill alone does not constitute inspecting a company.
