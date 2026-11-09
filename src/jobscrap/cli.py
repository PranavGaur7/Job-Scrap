"""Command entry point: construct dependencies, dispatch work, and report exit status."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys

from .observability import configure_logging as _configure_logging, log_failure

log = logging.getLogger(__name__)


def parser():
    """Describe commands without importing optional runtime integrations."""
    root = argparse.ArgumentParser(description="Verified recent jobs and resume matching")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="Verify Atlas connectivity and create indexes")
    commands.add_parser("doctor", help="Check configuration, PDF extraction and Atlas (no paid calls)")
    commands.add_parser("resume-info", help="Show detected skills, not private resume text")
    commands.add_parser("adapters", help="List installed, implemented adapters")
    companies = commands.add_parser("companies", help="Register companies individually or as a JSON list")
    company_commands = companies.add_subparsers(dest="company_command", required=True)
    company_commands.add_parser("list")
    add = company_commands.add_parser("add")
    add.add_argument("--id", required=True)
    add.add_argument("--name", required=True)
    add.add_argument("--url", required=True)
    add.add_argument("--adapter")
    add.add_argument("--config", type=Path, help="Adapter-specific JSON configuration")
    add.add_argument("--disabled", action="store_true")
    importer = company_commands.add_parser("import")
    importer.add_argument("file", type=Path)
    for operation in ("scrape", "rescore"):
        command = commands.add_parser(operation)
        command.add_argument("--company", help="Registered company id; otherwise all companies")
        if operation == "rescore":
            command.add_argument("--force", action="store_true", help="Bypass cache; may incur paid calls within the run budget")
    commands.add_parser("serve", help="Start dashboard; daily scheduling is a separate process")
    return root


def configure_logging(level):
    """Keep a single logging policy for CLI, timer and dashboard processes."""
    _configure_logging(level)


def main(argv=None):
    """Own process-level resources; library modules return data or raise errors."""
    args = parser().parse_args(argv)
    repository = None
    status = "success"
    try:
        from .config import Settings
        settings = Settings.from_env()
        configure_logging(settings.log_level)
        log.info("command_started command=%s", args.command)
        from .adapters.registry import discover_adapters
        from .matching import skills
        from .resume import load_resume

        if args.command == "adapters":
            print(json.dumps({"adapters": sorted(discover_adapters()),
                              "note": "No real company sources are bundled. Add verified adapters after inspection."}))
            return 0
        if args.command == "resume-info":
            resume = load_resume(settings.resume_path)
            print(json.dumps({"characters": len(resume.text), "skills": sorted(skills(resume.text))}, indent=2))
            return 0
        if args.command == "serve":
            import uvicorn
            from .web.app import create_app
            uvicorn.run(create_app(settings), host=settings.dashboard_host, port=settings.dashboard_port,
                        proxy_headers=False, access_log=False)
            return 0

        from .storage import connect
        if args.command == "doctor":
            resume = load_resume(settings.resume_path)
            print(f"Resume: readable ({len(resume.text)} characters)")
            print(f"Matching: {settings.matching_mode}; paid-call cap: {settings.ai_max_calls}/run")
            if settings.matching_mode == "hybrid":
                import importlib.util
                if importlib.util.find_spec("langchain") is None:
                    raise ValueError("Install the ai extra for hybrid matching")
                import os
                if settings.ai_provider == "openai" and not os.getenv("OPENAI_API_KEY"):
                    raise ValueError("OPENAI_API_KEY is required for the OpenAI provider")
            if not settings.mongodb_uri:
                raise ValueError("MONGODB_URI is not configured")
            repository = connect(settings)
            repository.ping()
            print("MongoDB: connected with certificate verification enabled")
            return 0
        repository = connect(settings)
        repository.ping()
        if args.command == "init-db":
            repository.ensure_indexes()
            log.info("database_initialization_finished")
            print("MongoDB connected; indexes ready")
        elif args.command == "companies":
            from .companies import validate_company
            if args.company_command == "list":
                print(json.dumps(repository.companies(), default=str, indent=2))
            else:
                adapters = discover_adapters()
                if args.company_command == "import":
                    values = json.loads(args.file.read_text())
                    if not isinstance(values, list) or not all(isinstance(v, dict) for v in values):
                        raise ValueError("Company import must be a JSON list of objects")
                else:
                    value = {"id": args.id, "name": args.name, "careers_url": args.url,
                             "adapter": args.adapter,
                             "adapter_config": json.loads(args.config.read_text()) if args.config else {}}
                    if args.disabled:
                        value["enabled"] = False
                    values = [value]
                # Validate the whole list before any writes.
                companies = [validate_company(value, adapters) for value in values]
                if len({c["_id"] for c in companies}) != len(companies):
                    raise ValueError("Import contains duplicate company IDs")
                for company in companies:
                    repository.put_company(company)
                log.info("companies_registered count=%s", len(companies))
                print(json.dumps({"registered": len(companies),
                                  "awaiting_adapter": sum(not c["adapter"] for c in companies)}))
        else:
            from .pipeline import execute
            result = execute(repository, settings, kind=args.command, company_id=args.company,
                             force=getattr(args, "force", False))
            print(json.dumps(result, default=str, indent=2))
            # Exhausting the chosen budget is normal; do not trigger paid systemd retries.
            failed = result["stats"]["company_errors"] or result["stats"]["ai_failures"]
            exit_code = 1 if failed or result["status"] == "failed" else 0
            status = "failed" if exit_code else result["status"]
            return exit_code
        return 0
    except KeyboardInterrupt:
        status = "interrupted"
        log.warning("command_interrupted command=%s", args.command)
        return 130
    except Exception as exc:
        status = "failed"
        from .locking import RunBusy
        if isinstance(exc, RunBusy):
            status = "busy"
            print("Another pipeline run is active; retry after it finishes", file=sys.stderr)
            return 75
        log_failure(log, "command_failed", exc)
        if isinstance(exc, ModuleNotFoundError):
            print(f"Missing dependency: {exc.name}. Activate .venv and install the project and needed extras; see README.md.",
                  file=sys.stderr)
            return 1
        # Explicit validation errors are safe; third-party errors may contain credentials.
        if isinstance(exc, (ValueError, FileNotFoundError)) and type(exc).__module__ == "builtins":
            print(f"Configuration/input error: {exc}", file=sys.stderr)
        else:
            print(f"Operation failed ({type(exc).__name__}); check configuration and logs", file=sys.stderr)
        return 1
    finally:
        if repository is not None:
            repository.close()
            log.debug("database_client_closed")
        log.info("command_finished command=%s status=%s", args.command, status)


if __name__ == "__main__":
    sys.exit(main())
