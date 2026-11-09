"""Validate company registration independently of scraper implementation."""
import re

from .domain import http_url


def _reject_config_secrets(value):
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            if any(secret in normalized for secret in ("apikey", "password", "secret", "token", "authorization", "credential")):
                raise ValueError("Adapter configuration must not contain secrets; use environment variables")
            _reject_config_secrets(child)
    elif isinstance(value, list):
        for child in value:
            _reject_config_secrets(child)


def validate_company(value, adapters):
    company_id = value.get("id", value.get("_id", ""))
    if not isinstance(company_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", company_id):
        raise ValueError("Company id must contain 1–80 lowercase letters, digits or hyphens")
    name = value.get("name", "").strip()
    if not name or len(name) > 200:
        raise ValueError("Company name is required (maximum 200 characters)")
    url = http_url(value.get("careers_url", ""))
    adapter_name = value.get("adapter") or None
    if adapter_name and adapter_name not in adapters:
        raise ValueError(f"Unknown adapter: {adapter_name}. Inspect and implement this source first")
    config = value.get("adapter_config", {})
    if not isinstance(config, dict):
        raise ValueError("adapter_config must be an object")
    _reject_config_secrets(config)
    enabled = value.get("enabled", bool(adapter_name))
    if not isinstance(enabled, bool) or (enabled and not adapter_name):
        raise ValueError("Only companies with implemented adapters can be enabled")
    result = {"_id": company_id, "name": name, "careers_url": url, "adapter": adapter_name,
              "adapter_config": config, "enabled": enabled,
              "integration_status": "configured" if adapter_name else "awaiting_adapter"}
    if adapter_name:
        adapters[adapter_name].validate_config(result)
    return result
