from __future__ import annotations
import time
import json
from pathlib import Path
import requests
import logging
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from config import (
    FRESHDESK_DOMAIN,
    FRESHDESK_API_KEY,
    HTTP_TIMEOUT,
    PORTAL_TICKET_FORM_URL,
)

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


# --- Portal scraping ------------------------------------------------------

def _extract_field_key(raw_name: str | None) -> str | None:
    """Return the Freshdesk field key from a portal form name.

    Examples::

        "helpdesk_ticket[email]" -> "email"
        "helpdesk_ticket[custom_field][cf_platform]" -> "cf_platform"
    """
    if not raw_name:
        return None
    if not raw_name.startswith("helpdesk_ticket["):
        return raw_name
    inner = raw_name[len("helpdesk_ticket[") :].rstrip("]")
    parts = inner.split("][")
    if parts and parts[0] == "custom_field" and len(parts) > 1:
        return parts[1]
    return parts[0] if parts else None


def _scrape_portal_fields() -> list[dict]:
    """Parse ticket fields from the public Freshdesk portal form.

    The portal is treated as the source of truth for field order, labels, and
    option text. We scrape the rendered form and return a simplified structure
    that can later be matched against API field metadata.
    """

    try:
        resp = requests.get(PORTAL_TICKET_FORM_URL, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        log.error("❌ Failed to fetch portal form %s (%s)", PORTAL_TICKET_FORM_URL, e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    form = soup.find("form")
    if not form:
        return []

    fields: list[dict] = []
    # Each ``control-group`` represents a single ticket field. Iterating over
    # these containers preserves the visual order from the portal.
    for group in form.select(".control-group"):
        label = group.find("label")
        input_el = group.find(["input", "select", "textarea"])
        if not label or input_el is None:
            continue

        raw_name = input_el.get("name")
        field_key = _extract_field_key(raw_name)
        if not field_key:
            continue

        field_type = input_el.name
        choices = []
        if field_type == "select":
            for opt in input_el.find_all("option"):
                value = opt.get("value")
                text = opt.get_text(strip=True)
                if value is None:
                    continue
                choices.append({
                    "value": value,
                    "label": text,
                    "id": opt.get("data-id"),
                })

        fields.append(
            {
                "name": field_key,
                "label": label.get_text(strip=True),
                "type": field_type,
                "choices": choices,
            }
        )

    return fields


# --- Cached helpers ------------------------------------------------------
_FORMS_CACHE: dict[str, object] = {"expires": 0, "data": []}
_FIELDS_CACHE: dict[str, object] = {"expires": 0, "data": []}

# Attempt to warm the fields cache from the bundled JSON snapshot. If the
# file exists we load it as a starting point but still refresh from the live
# API on first use so that updates in Freshdesk are reflected in the question
# flow. Missing or malformed files simply fall back to the live API.
_FIELDS_FILE = Path(__file__).resolve().parent.parent / "ticket_fields.json"
if _FIELDS_FILE.exists():
    try:
        with _FIELDS_FILE.open("r", encoding="utf-8") as fh:
            _FIELDS_CACHE["data"] = json.load(fh)
            # expire immediately so fresh data is fetched on first access
            _FIELDS_CACHE["expires"] = 0
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

    def _merge(portal_fields: list[dict], api_fields: list[dict]):
        by_name = {f.get("name"): f for f in api_fields}
        merged: list[dict] = []
        for pf in portal_fields:
            api_f = by_name.get(pf.get("name"))
            if api_f:
                # Use API data but keep portal label and option order
                api_f = api_f.copy()
                api_f["label"] = pf.get("label") or api_f.get("label")
                if pf.get("choices") and isinstance(api_f.get("choices"), list):
                    choices_map = {c.get("value"): c for c in api_f["choices"]}
                    new_choices = []
                    for opt in pf["choices"]:
                        c = choices_map.get(opt.get("value")) or {}
                        new_choices.append({
                            "value": c.get("value", opt.get("value")),
                            "label": opt.get("label", c.get("label")),
                        })
                    api_f["choices"] = new_choices
                merged.append(api_f)
            else:
                merged.append(pf)
        return merged

    if now >= _FIELDS_CACHE["expires"]:
        portal_fields = _scrape_portal_fields()
        try:
            api_fields = fd_get("/api/v2/admin/ticket_fields")
            _FIELDS_CACHE["data"] = _merge(portal_fields, api_fields) if portal_fields else api_fields
        except Exception as e:
            if portal_fields:
                log.warning("Ticket fields API failed (%s); using portal scrape only", e)
                _FIELDS_CACHE["data"] = portal_fields
            else:
                log.warning("Ticket fields unavailable (%s)", e)
                _FIELDS_CACHE["data"] = []
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
