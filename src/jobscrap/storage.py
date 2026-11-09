from __future__ import annotations

from functools import wraps
import logging
import random
import re
import time

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import (
    AutoReconnect, ConnectionFailure, DuplicateKeyError, NetworkTimeout,
    PyMongoError, ServerSelectionTimeoutError, OperationFailure,
)

from .config import Settings
from .domain import JobListing, WINDOW, utcnow

log = logging.getLogger(__name__)


class StorageUnavailable(RuntimeError):
    pass


class IdentityConflict(ValueError):
    pass


def retry_db(operation):
    @wraps(operation)
    def wrapped(*args, **kwargs):
        for attempt in range(3):
            try:
                return operation(*args, **kwargs)
            except (ConnectionFailure, AutoReconnect, NetworkTimeout, ServerSelectionTimeoutError) as exc:
                if attempt == 2:
                    raise StorageUnavailable("MongoDB unavailable after bounded retries") from exc
                log.warning("Transient database error (%s); retry %s/2", type(exc).__name__, attempt + 1)
                time.sleep(0.25 * 2**attempt + random.uniform(0, 0.1))
            except OperationFailure as exc:
                if (exc.has_error_label("RetryableWriteError") or exc.code in {91, 189, 11600, 11602}) and attempt < 2:
                    time.sleep(0.25 * 2**attempt)
                    continue
                raise StorageUnavailable("MongoDB operation failed; check permissions and server logs") from exc
            except PyMongoError as exc:
                raise StorageUnavailable("MongoDB operation failed; check configuration") from exc
    return wrapped


def connect(settings: Settings):
    import certifi

    if not settings.mongodb_uri:
        raise StorageUnavailable("MONGODB_URI is not configured")
    try:
        client = MongoClient(
            settings.mongodb_uri, tz_aware=True, tls=True, tlsCAFile=certifi.where(),
            tlsAllowInvalidCertificates=False, tlsAllowInvalidHostnames=False,
            serverSelectionTimeoutMS=settings.mongodb_timeout_ms,
            connectTimeoutMS=settings.mongodb_timeout_ms,
            socketTimeoutMS=settings.mongodb_timeout_ms,
            timeoutMS=settings.mongodb_timeout_ms * 2,
            retryWrites=True, retryReads=True, appname="jobscrap",
        )
    except (PyMongoError, ValueError) as exc:
        raise StorageUnavailable("Invalid MongoDB configuration") from exc
    repository = MongoRepository(client[settings.mongodb_database], client)
    return repository


class MongoRepository:
    def __init__(self, database, client=None):
        self.db, self.client = database, client

    def close(self):
        if self.client is not None:
            self.client.close()

    @retry_db
    def ping(self):
        self.db.command("ping")

    @retry_db
    def ensure_indexes(self):
        self.db.companies.create_index("name", name="company_name")
        self.db.jobs.create_index(
            [("company_id", ASCENDING), ("external_job_id", ASCENDING)],
            unique=True, name="company_external_id_unique",
            partialFilterExpression={"external_job_id": {"$type": "string"}},
        )
        self.db.jobs.create_index(
            [("company_id", ASCENDING), ("canonical_url", ASCENDING)],
            unique=True, name="company_url_unique",
        )
        self.db.jobs.create_index(
            [("date_verified", ASCENDING), ("active", ASCENDING), ("posted_at", DESCENDING)],
            name="recent_verified_jobs",
        )
        self.db.scraping_history.create_index("started_at", name="history_started")
        self.db.scraping_history.create_index([("company_id", ASCENDING), ("started_at", DESCENDING)])
        # _id is the unique cache key; keep cache/history until an explicit retention policy is chosen.

    @retry_db
    def put_company(self, company):
        values = {key: value for key, value in company.items() if key != "_id"}
        self.db.companies.update_one(
            {"_id": company["_id"]},
            {"$set": {**values, "updated_at": utcnow()}, "$setOnInsert": {"created_at": utcnow()}},
            upsert=True,
        )

    @retry_db
    def companies(self, company_id=None, *, enabled_only=False):
        query = {"_id": company_id} if company_id else {}
        if enabled_only:
            query["enabled"] = True
        return list(self.db.companies.find(query).sort("name", ASCENDING))

    def _existing(self, company_id, listing):
        by_url = self.db.jobs.find_one({"company_id": company_id, "canonical_url": listing.canonical_url})
        by_id = (self.db.jobs.find_one({"company_id": company_id, "external_job_id": listing.external_job_id})
                 if listing.external_job_id else None)
        if by_url and by_id and by_url["_id"] != by_id["_id"]:
            raise IdentityConflict("ATS identity and URL resolve to different records")
        if by_url and listing.external_job_id and by_url.get("external_job_id") not in {None, listing.external_job_id}:
            raise IdentityConflict("Application URL reused for different ATS IDs; adapter must use a job-specific URL")
        return by_id or by_url

    @retry_db
    def upsert_job(self, company, listing: JobListing, now=None):
        now = now or utcnow()
        if listing.posting_date is None or not listing.posting_date.recent(now):
            raise ValueError("Only verified recent jobs can be inserted")
        for attempt in range(2):
            existing = self._existing(company["_id"], listing)
            changed = not existing or existing["content_hash"] != listing.content_hash
            fields = {
                "company_id": company["_id"], "company_name": company["name"],
                "title": listing.title, "location": listing.location,
                "description": listing.description, "application_url": listing.application_url,
                "canonical_url": listing.canonical_url, "content_hash": listing.content_hash,
                "posted_at": listing.posting_date.posted_at,
                "posting_evidence": listing.posting_date.document(), "date_verified": True,
                "last_seen_at": now, "active": True,
            }
            if listing.external_job_id:
                fields["external_job_id"] = listing.external_job_id
            if changed:
                fields["content_changed_at"] = now
                if existing:
                    fields["previous_content_hash"] = existing["content_hash"]
            identity = existing["_id"] if existing else listing.identity(company["_id"])
            update = {"$set": fields, "$setOnInsert": {"first_seen_at": now}}
            if changed:
                update["$unset"] = {"match": ""}
            try:
                self.db.jobs.update_one({"_id": identity}, update, upsert=True)
                return self.db.jobs.find_one({"_id": identity}), changed
            except DuplicateKeyError:
                if attempt:
                    raise IdentityConflict("Concurrent identity conflict; investigate job source") from None
        raise AssertionError("unreachable")

    @retry_db
    def exclude_listing(self, company_id, listing, now):
        existing = self._existing(company_id, listing)
        if existing:
            self.db.jobs.update_one({"_id": existing["_id"]}, {"$set": {
                "date_verified": False, "last_seen_at": now,
                "date_exclusion_reason": "missing, future, or outside verified 72-hour window",
            }})
        return existing["_id"] if existing else None

    @retry_db
    def mark_missing(self, company_id, seen_ids):
        self.db.jobs.update_many(
            {"company_id": company_id, "_id": {"$nin": list(seen_ids)}, "active": True},
            {"$set": {"active": False, "closed_detected_at": utcnow()}},
        )

    @retry_db
    def set_match(self, job_id, content_hash, match):
        # Compare-and-set prevents a stale result overwriting a newly changed listing.
        self.db.jobs.update_one({"_id": job_id, "content_hash": content_hash}, {"$set": {"match": match}})

    @retry_db
    def get_cached_score(self, cache_key):
        return self.db.match_cache.find_one({"_id": cache_key})

    @retry_db
    def cache_score(self, score):
        values = {key: value for key, value in score.items() if key != "_id"}
        self.db.match_cache.update_one({"_id": score["_id"]}, {"$set": values}, upsert=True)

    @retry_db
    def recent_jobs(self, *, now=None, company_id=None, limit=100, offset=0,
                    search="", location="", min_score=0, resume_hash=None, signature=None, sort="newest"):
        now = now or utcnow()
        query = {"date_verified": True, "active": True, "posted_at": {"$gte": now - WINDOW, "$lte": now}}
        if company_id:
            query["company_id"] = company_id
        if search:
            query["$or"] = [{field: {"$regex": re.escape(search), "$options": "i"}}
                            for field in ("title", "company_name", "description")]
        if location:
            query["location"] = {"$regex": re.escape(location), "$options": "i"}
        if min_score > 0 or sort == "score":
            query.update({"match.score": {"$gte": min_score}, "match.resume_hash": resume_hash,
                          "match.scorer_signature": signature,
                          "$expr": {"$eq": ["$match.content_hash", "$content_hash"]}})
        ordering = ([("match.score", DESCENDING), ("posted_at", DESCENDING), ("_id", ASCENDING)]
                    if sort == "score" else [("posted_at", DESCENDING), ("_id", ASCENDING)])
        return list(self.db.jobs.find(query).sort(
            ordering
        ).skip(offset).limit(limit))

    @retry_db
    def start_run(self, run):
        self.db.scraping_history.update_one({"_id": run["_id"]}, {"$setOnInsert": run}, upsert=True)

    @retry_db
    def finish_run(self, run_id, fields):
        self.db.scraping_history.update_one({"_id": run_id}, {"$set": {**fields, "finished_at": utcnow()}})

    @retry_db
    def recent_runs(self, limit=10):
        return list(self.db.scraping_history.find().sort("started_at", DESCENDING).limit(limit))

    @retry_db
    def recover_interrupted_runs(self):
        # Only called under the shared local file lock: no live pipeline can own these runs.
        self.db.scraping_history.update_many(
            {"status": "running"}, {"$set": {"status": "interrupted", "finished_at": utcnow()}},
        )

    @retry_db
    def set_metadata(self, key, fields):
        self.db.metadata.update_one({"_id": key}, {"$set": fields}, upsert=True)
