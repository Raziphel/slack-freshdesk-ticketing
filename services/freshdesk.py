from __future__ import annotations
import time
import json
from pathlib import Path
import requests
import logging
from requests.adapters import HTTPAdapter
from config import FRESHDESK_DOMAIN, FRESHDESK_API_KEY, HTTP_TIMEOUT

log = logging.getLogger(__name__)


_session = requests.Session()
_session.auth = (FRESHDESK_API_KEY, "X")
_session.mount("https://", HTTPAdapter(pool_connections=20, pool_maxsize=20))


def fd_get(path: str):
    url = f"https://{FRESHDESK_DOMAIN}.freshdesk.com{path}"
    r = _session.get(url, timeout=HTTP_TIMEOUT)
    if not r.ok:
        log.error("❌ FD GET %s -> %s", path, r.text[:800])
    r.raise_for_status()
    return r.json()


def fd_post(path: str, payload: dict):
    url = f"https://{FRESHDESK_DOMAIN}.freshdesk.com{path}"
    r = _session.post(url, json=payload, timeout=HTTP_TIMEOUT)
    if not r.ok:
        log.error("❌ FD POST %s -> %s", path, r.text[:800])
    r.raise_for_status()
    return r.json()


# --- Cached helpers ------------------------------------------------------
_FORMS_CACHE: dict[str, object] = {"expires": 0, "data": []}
_FIELDS_CACHE: dict[str, object] = {"expires": 0, "data": []}

# Attempt to warm the fields cache from the bundled JSON snapshot. If the
# file exists we keep the data indefinitely and avoid an API call during
# startup. Missing or malformed files simply fall back to the live API.
_FIELDS_FILE = Path(__file__).resolve().parent.parent / "ticket_fields.json"
if _FIELDS_FILE.exists():
    try:
        with _FIELDS_FILE.open("r", encoding="utf-8") as fh:
            _FIELDS_CACHE["data"] = json.load(fh)
            _FIELDS_CACHE["expires"] = float("inf")
            log.info("Loaded %d ticket fields from %s", len(_FIELDS_CACHE["data"]), _FIELDS_FILE)
    except Exception as e:  # pragma: no cover - best effort only
        log.warning("Failed to load %s: %s", _FIELDS_FILE, e)


def get_ticket_forms_cached(ttl: int = 300):
    now = time.time()
    if now >= _FORMS_CACHE["expires"]:
        _FORMS_CACHE["data"] = fd_get("/api/v2/ticket-forms")
        _FORMS_CACHE["expires"] = now + ttl
    return _FORMS_CACHE["data"]


def get_ticket_fields_cached(ttl: int = 300):
    now = time.time()
    if now >= _FIELDS_CACHE["expires"]:
        _FIELDS_CACHE["data"] = fd_get("/api/v2/admin/ticket_fields")
        _FIELDS_CACHE["expires"] = now + ttl
    return _FIELDS_CACHE["data"]


def get_form_detail(form_id: int):
    return fd_get(f"/api/v2/ticket-forms/{form_id}")


def get_sections(field_id: int):
    path = f"/api/v2/admin/ticket_fields/{field_id}/sections"
    url = f"https://{FRESHDESK_DOMAIN}.freshdesk.com{path}"
    try:
        r = _session.get(url, timeout=HTTP_TIMEOUT)
        if not r.ok:
            # Freshdesk returns 400/404/422 when a field has no
            # conditional sections configured. These responses are
            # expected during wizard traversal so we quietly treat
            # them as "no sections" instead of logging noisy errors.
            log.debug("No sections for field %s (%s)", field_id, r.status_code)
            return []
        return r.json() or []
    except Exception as e:
        log.debug("No sections for field %s (%s)", field_id, e)
        return []


def fetch_field_detail(field_id: int) -> dict | None:
    try:
        return fd_get(f"/api/v2/admin/ticket_fields/{field_id}")
    except Exception as e:
        logging.info("No detail for field %s (%s)", field_id, e)
        return None
