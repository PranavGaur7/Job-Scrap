"""Jobs, source evidence and identity rules, independent of network/database code.

These objects enforce business invariants before external data reaches storage.
An adapter still has to prove that its source field really means "posted at".
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

UTC = timezone.utc
WINDOW = timedelta(days=3)


def utcnow():
    return datetime.now(UTC)


def http_url(value: str) -> str:
    """Reject unsafe schemes and embedded credentials at the data boundary."""
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Expected an HTTP(S) URL without embedded credentials")
    if any(ord(c) < 33 for c in value):
        raise ValueError("URL contains whitespace or control characters")
    _ = parts.port  # Validate malformed ports too.
    return value.strip()


def canonical_url(value: str) -> str:
    """Normalize a job's URL without deleting query parameters that identify it."""
    parts = urlsplit(http_url(value))
    hostname = parts.hostname.lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    port = parts.port
    netloc = hostname if port in {None, 443 if parts.scheme == "https" else 80} else f"{hostname}:{port}"
    tracking = {"gclid", "fbclid", "msclkid", "mc_cid", "mc_eid"}
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in tracking]
    # Functional query parameters, path case, and trailing slashes are preserved.
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", urlencode(sorted(query)), ""))


def plain_text(value: str) -> str:
    """Remove basic source markup and normalize whitespace for display/hashing.

    This is text normalization, not an HTML security sanitizer. Templates must
    still escape rendered values rather than mark source content as safe HTML.
    """
    return " ".join(html.unescape(re.sub(r"<[^>]*>", " ", value)).split())


def digest(value) -> str:
    """Fingerprint deterministic JSON; a fingerprint identifies content, not secrecy."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class DateKind(str, Enum):
    ATS_API = "ats_api"
    STRUCTURED_DATA = "structured_data"
    VISIBLE_POSTED_DATE = "visible_posted_date"


@dataclass(frozen=True)
class PostingDate:
    """A UTC posting timestamp with evidence explaining where it came from."""
    posted_at: datetime
    raw_value: str
    source_url: str
    field_name: str
    kind: DateKind
    precision: str = "timestamp"

    def __post_init__(self):
        # Keep provenance next to the timestamp so discovery/update dates cannot
        # silently be substituted for posting evidence during later processing.
        http_url(self.source_url)
        if not self.raw_value.strip() or not self.field_name.strip():
            raise ValueError("Posting date requires raw source evidence and field name")
        field = re.sub(r"[^a-z]", "", self.field_name.lower())
        if any(word in field for word in ("updated", "modified", "firstseen", "lastseen", "scraped")):
            raise ValueError("Update/discovery times cannot establish posting dates")
        if self.posted_at.tzinfo is None or self.posted_at.utcoffset() is None:
            raise ValueError("Posting timestamps must be timezone-aware")
        object.__setattr__(self, "posted_at", self.posted_at.astimezone(UTC))
        object.__setattr__(self, "kind", DateKind(self.kind))
        if self.precision not in {"date", "timestamp"}:
            raise ValueError("Invalid posting-date precision")

    @classmethod
    def from_iso(cls, raw: str, *, source_url: str, field_name: str,
                 kind: DateKind, source_timezone: str | None = None):
        """Adapters must establish semantics. Never parse relative/ambiguous dates."""
        raw = raw.strip()
        date_only = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw))
        if not date_only and not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?", raw
        ):
            raise ValueError("Use an explicit ISO posting date, not a relative/ambiguous date")
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            if not source_timezone:
                raise ValueError("A source timezone is required for date-only/naive values")
            parsed = parsed.replace(tzinfo=ZoneInfo(source_timezone))
            # Reject nonexistent or ambiguous wall times rather than guessing DST folds.
            if parsed.astimezone(UTC).astimezone(parsed.tzinfo).replace(tzinfo=None) != parsed.replace(tzinfo=None):
                raise ValueError("Nonexistent local posting time")
            if parsed.replace(fold=0).utcoffset() != parsed.replace(fold=1).utcoffset():
                raise ValueError("Ambiguous local posting time")
        return cls(parsed, raw, source_url, field_name, kind, "date" if date_only else "timestamp")

    def recent(self, now: datetime) -> bool:
        # Date-only sources use start-of-day in the documented source timezone.
        # This conservative lower bound excludes days straddling the 72h cutoff.
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return now - WINDOW <= self.posted_at <= now

    def document(self):
        result = asdict(self)
        result["kind"] = self.kind.value
        return result


@dataclass(frozen=True)
class JobListing:
    """Common adapter output before company details and storage metadata are added."""
    title: str
    location: str
    description: str
    application_url: str
    posting_date: PostingDate | None
    external_job_id: str | None = None

    def __post_init__(self):
        http_url(self.application_url)
        for name in ("title", "location", "description"):
            object.__setattr__(self, name, plain_text(getattr(self, name)))
        if not self.title or not self.description:
            raise ValueError("Jobs require a title and description")
        if self.external_job_id is not None:
            value = str(self.external_job_id).strip()
            object.__setattr__(self, "external_job_id", value or None)

    @property
    def canonical_url(self):
        return canonical_url(self.application_url)

    @property
    def content_hash(self):
        # Provenance, timestamps, URLs and tracking changes do not trigger paid scoring.
        return digest([self.title, self.location, self.description])

    def identity(self, company_id):
        """Keep identity stable across edits; ATS IDs are scoped to a company.

        Content hashes answer "did this change?"; identity answers "which job?".
        Storage preserves an existing _id when a URL-only record later gains an ATS ID.
        """
        return digest([company_id, "ats" if self.external_job_id else "url",
                       self.external_job_id or self.canonical_url])
