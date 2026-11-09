"""Personal dashboard: read persisted jobs and request the shared pipeline.

Page reads do not scrape or score. Manual actions run in a separate thread so a
source timeout does not occupy the web request that submitted the action.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from functools import lru_cache
import logging
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import Settings
from ..domain import utcnow
from ..matching import scorer_signature
from ..observability import log_context, log_failure
from ..resume import load_resume
from ..storage import StorageUnavailable, connect

log = logging.getLogger(__name__)
ROOT = Path(__file__).parent
security = HTTPBasic(auto_error=False)


@lru_cache(maxsize=2)
def _resume_hash(path, mtime, size):
    """Invalidate the small local PDF cache when the underlying file changes."""
    return load_resume(Path(path)).content_hash


def create_app(settings=None, repository=None, runner=None):
    """Construct routes and resources; injected collaborators support offline tests."""
    settings = settings or Settings.from_env()
    task_guard = threading.Lock()
    task = {"future": None, "message": ""}

    def authenticate(credentials: HTTPBasicCredentials | None = Depends(security)):
        if not settings.dashboard_password:
            return
        if (credentials is None or
            not secrets.compare_digest(credentials.username.encode(), settings.dashboard_username.encode()) or
            not secrets.compare_digest(credentials.password.encode(), settings.dashboard_password.encode())):
            raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Basic"})

    @asynccontextmanager
    async def lifespan(app):
        # Reuse one read client while the server lives; each manual pipeline owns
        # its own client so its cleanup does not interrupt dashboard reads.
        log.info("dashboard_starting")
        app.state.repository = repository
        if repository is None and settings.mongodb_uri:
            try:
                app.state.repository = connect(settings)
            except StorageUnavailable as exc:
                log_failure(log, "dashboard_database_configuration_failed", exc)
        app.state.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jobscrap-manual")
        log.info("dashboard_started database_configured=%s", app.state.repository is not None)
        yield
        app.state.executor.shutdown(wait=True, cancel_futures=True)
        if repository is None and app.state.repository is not None:
            app.state.repository.close()
        log.info("dashboard_stopped")

    app = FastAPI(title="JobScrap", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=ROOT / "templates")

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    def health(request: Request):
        repo = request.app.state.repository
        if repo is None:
            raise HTTPException(503, "Database unavailable")
        try:
            repo.ping()
        except StorageUnavailable:
            raise HTTPException(503, "Database unavailable") from None
        return {"status": "ok"}

    @app.get("/", dependencies=[Depends(authenticate)])
    def dashboard(request: Request, q: str = Query("", max_length=150), company: str = Query("", max_length=80),
                  location: str = Query("", max_length=100), min_score: float = Query(0, ge=0, le=100),
                  sort: str = Query("newest", pattern="^(newest|score)$"), page: int = Query(1, ge=1, le=10000)):
        """Query a page and suppress assessments for outdated resume/job content."""
        repo = request.app.state.repository
        jobs, companies, runs, warning = [], [], [], ""
        resume_hash = None
        try:
            stat = settings.resume_path.stat()
            resume_hash = _resume_hash(str(settings.resume_path), stat.st_mtime_ns, stat.st_size)
        except Exception as exc:
            log_failure(log, "dashboard_resume_unavailable", exc, level=logging.DEBUG)
            warning = "Resume unavailable. Check RESUME_PATH; scores will show as pending."
        signature = scorer_signature(settings)
        if repo is None:
            warning = "MongoDB is not configured or its configuration is invalid. Set MONGODB_URI and restart the dashboard."
        else:
            try:
                companies = repo.companies()
                jobs = repo.recent_jobs(company_id=company or None, search=q, location=location,
                                        min_score=min_score, resume_hash=resume_hash, signature=signature,
                                        sort=sort, limit=26, offset=(page - 1) * 25)
                runs = repo.recent_runs()
            except StorageUnavailable as exc:
                log_failure(log, "dashboard_query_failed", exc)
                warning = "MongoDB is temporarily unavailable. Check Atlas access and connectivity, then refresh."
        has_next = len(jobs) > 25
        jobs = jobs[:25]
        for job in jobs:
            # An old numeric score may still exist in storage. Only display it
            # when all inputs match the current scoring configuration.
            match = job.get("match") or {}
            job["display_match"] = match if (
                match.get("resume_hash") == resume_hash and resume_hash is not None and
                match.get("content_hash") == job.get("content_hash") and
                match.get("scorer_signature") == signature
            ) else None
        csrf = request.cookies.get("jobscrap_csrf") or secrets.token_urlsafe(32)
        params = {"q": q, "company": company, "location": location, "min_score": min_score, "sort": sort}
        response = templates.TemplateResponse(request=request, name="dashboard.html", context={
            "jobs": jobs, "companies": companies, "runs": runs, "warning": warning,
            "q": q, "company": company, "location": location, "min_score": min_score, "sort": sort,
            "page": page, "has_next": has_next, "csrf": csrf, "mode": settings.matching_mode,
            "enabled_count": sum(c["enabled"] for c in companies), "now": utcnow(),
            "task_message": task["message"], "db_ready": repo is not None and not warning,
            "previous": "/?" + urlencode({**params, "page": max(1, page - 1)}),
            "next": "/?" + urlencode({**params, "page": page + 1}),
        })
        response.set_cookie("jobscrap_csrf", csrf, httponly=True, samesite="strict", secure=request.url.scheme == "https")
        return response

    def work(kind):
        """Background worker boundary: log safe failures and provide a UI status."""
        with log_context(operation=kind):
            started = time.monotonic()
            log.info("manual_run_started operation=%s", kind)
            try:
                if runner:
                    result = runner(kind)
                else:
                    from ..pipeline import execute
                    repo = connect(settings)
                    try:
                        result = execute(repo, settings, kind=kind)
                    finally:
                        repo.close()
                task["message"] = f"{kind.capitalize()} finished: {result['status']}. See run history."
                log.info("manual_run_finished operation=%s status=%s elapsed_ms=%.1f",
                         kind, result["status"], (time.monotonic() - started) * 1000)
            except Exception as exc:
                from ..locking import RunBusy
                task["message"] = ("A scheduled/manual run is already active. Try again when it finishes."
                                   if isinstance(exc, RunBusy) else f"Manual run failed ({type(exc).__name__}). Check logs.")
                log_failure(log, f"manual_run_failed operation={kind}", exc)

    @app.post("/actions/{kind}", dependencies=[Depends(authenticate)])
    async def action(kind: str, request: Request):
        """Validate a write request before scheduling a single background task."""
        if kind not in {"scrape", "rescore"}:
            raise HTTPException(404, "Unknown operation")
        # SameSite + double-submit token and origin validation protect Basic-auth sessions.
        origin = request.headers.get("origin")
        if origin and (urlsplit(origin).scheme, urlsplit(origin).netloc) != (request.url.scheme, request.url.netloc):
            log.warning("manual_action_rejected reason=foreign_origin")
            raise HTTPException(403, "Origin rejected")
        if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
            raise HTTPException(415, "Expected a form submission")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 4096:
                raise HTTPException(413, "Form too large")
        form = parse_qs(body.decode("utf-8", errors="replace"))
        token = form.get("csrf", [""])[0]
        cookie = request.cookies.get("jobscrap_csrf", "")
        if not cookie or not secrets.compare_digest(token, cookie):
            log.warning("manual_action_rejected reason=invalid_csrf")
            raise HTTPException(403, "Invalid form token; refresh the dashboard")
        if request.app.state.repository is None:
            raise HTTPException(503, "Configure MongoDB first")
        with task_guard:
            if task["future"] is not None and not task["future"].done():
                log.warning("manual_action_rejected reason=worker_busy")
                raise HTTPException(409, "A manual run is already active")
            task["message"] = f"{kind.capitalize()} requested. Refresh to see progress."
            task["future"] = request.app.state.executor.submit(work, kind)
            log.info("manual_action_accepted operation=%s", kind)
        return RedirectResponse("/", status_code=303)

    return app
