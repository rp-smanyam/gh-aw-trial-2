"""Knock property → timezone lookup with in-process caching.

Resident product_info `property_timezone` is missing from voice/chat
trace metadata, so without this lookup the downstream brief SP falls
back to UTC. The Knock admin API is the authoritative source.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

ADMIN_PROPERTY_PATH = "/v1/admin/property/{property_id}"


class PropertyTimezoneLookup:
    """Synchronous, threadless lookup. One process, one cache.

    Caches None too — a 404 or missing field is a stable answer for the run.
    """

    def __init__(self, token: str | None, *, base_url: str | None = None, timeout: float = 10.0) -> None:
        self._token = token
        self._base_url = (base_url or os.environ.get("KNOCK_INTERNAL_API_URL") or "").rstrip("/")
        self._timeout = timeout
        self._cache: dict[str, str | None] = {}
        self.hits = 0
        self.api_calls = 0
        self.errors = 0

    def get(self, property_id: str | None) -> str | None:
        """Return the IANA timezone string, or None.

        Callers should treat None as "leave the field unset" — never substitute
        a guess like UTC.
        """
        if not property_id:
            return None
        if property_id in self._cache:
            self.hits += 1
            return self._cache[property_id]
        if not self._token or not self._base_url:
            self._cache[property_id] = None
            return None

        self.api_calls += 1
        tz = self._fetch_timezone(property_id)
        self._cache[property_id] = tz
        return tz

    def _fetch_timezone(self, property_id: str) -> str | None:
        url = self._base_url + ADMIN_PROPERTY_PATH.format(property_id=property_id)
        req = urllib.request.Request(url, headers={"Internal-Authorization": f"Bearer {self._token}"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = json.load(resp)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
            self.errors += 1
            return None
        # Prefer top-level `property.timezone`; nested copy is a safety net.
        prop = body.get("property") if isinstance(body, dict) else None
        if not isinstance(prop, dict):
            return None
        tz = prop.get("timezone")
        if isinstance(tz, str) and tz:
            return tz
        try:
            nested = prop["data"]["location"]["timezone"]
        except (KeyError, TypeError):
            return None
        return nested if isinstance(nested, str) and nested else None

    def stats(self) -> str:
        return (
            f"property_timezone lookup: cache_hits={self.hits} "
            f"api_calls={self.api_calls} errors={self.errors} "
            f"unique_properties={len(self._cache)}"
        )
