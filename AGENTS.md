# Project Instructions

- Always read PROJECT_MEMORY.md before development.
- Update project memory after significant changes.
- Use Python with venv and simple, modular architecture.
- Keep scraping, storage, matching, scheduling, and UI separate.
- Prefer deterministic logic over unnecessary AI calls.
- Prefer official ATS APIs over browser scraping.
- Include only jobs with verified posting dates within the last 3 days.
- Deduplicate jobs by stable ATS ID or canonical URL.
- Use LangChain with interchangeable AI providers.
- Store API keys in .env, never in source control.
- Add tests for new functionality.
- Do not overengineer or invent working integrations.
- Follow the modular `src/jobscrap` architecture; company adapters export `ADAPTER` or use `jobscrap.adapters` entry points. Do not add company-specific branches to the pipeline.
- Production scraping must use standalone Python. Development Playwright MCP is never a production dependency.
- Preserve posting-date evidence. Never substitute update, discovery, first-seen, or scrape dates for posting dates.
- Maintain both company/ATS-ID and company/canonical-URL uniqueness; keep posting and discovery times separate.
- CLI, dashboard manual actions and systemd must share `STATE_DIR` and the local pipeline lock on one host.
- Run `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v` and compile checks after changes; report skipped suites and live verification limits explicitly.
- Before enabling a real company, inspect its source using the company-career-scraper skill and add sanitized fixture tests plus a standalone live verification. Register unimplemented sources as disabled `awaiting_adapter` records.
- Do not store raw resume text or credentials in MongoDB/logs; hybrid matching sends redacted resume/job content to the configured provider only when enabled.

## Learning and collaboration

- The user is learning software engineering through this project. Teach using the actual existing files, functions, tests and data flow rather than replacing working code with a teaching implementation.
- Continue authorized feature development normally while explaining significant new concepts, architectural decisions, trade-offs and relevant alternatives concisely. Do not explain basic programming syntax unless asked.
- Do not rebuild, refactor or modify application code merely to support a lesson. Respect requests to focus on understanding without adding features.
- Maintain `LEARNING_ROADMAP.md` with modules, concepts, code references, hands-on exercises and evidence of learning progress. Read it before teaching or updating learning progress.
- After each module, give a short quiz and a small coding or debugging exercise for the user to complete. Leave the exercise unsolved until the user submits an attempt or explicitly requests a solution.
- Review answers and code critically: distinguish correct reasoning from guesses, explain specific misconceptions, check meaningful behavior and test coverage, and request targeted revisions when needed. Do not mark a module complete just because a lesson was presented or tests passed.
- Record lesson delivery, pending work, reviewed attempts and demonstrated understanding in the roadmap. Pending learning exercises do not block separately authorized feature work.
- Keep `PROJECT_MEMORY.md` focused on implementation, configuration, verification and next development steps. Store learning notes and assessments only in `LEARNING_ROADMAP.md`.
- Add concise module/function documentation and comments explaining important invariants, decisions and failure cases; avoid narrating basic syntax or every line.
- Use the shared `jobscrap.observability` logging policy. Keep diagnostics on stderr, add trusted run/company/job identifiers to context, and never interpolate raw exceptions, credentials, resume text or source payloads into logs.
