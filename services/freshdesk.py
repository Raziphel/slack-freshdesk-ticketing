from __future__ import annotations
import time
import json
import re
from pathlib import Path
import requests
import logging
import os
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from config import (
    FRESHDESK_DOMAIN,
    FRESHDESK_API_KEY,
    HTTP_TIMEOUT,
    PORTAL_TICKET_FORM_URL,
)

log = logging.getLogger(__name__)

FD_DEBUG_SCRAPE = os.getenv("FD_DEBUG_SCRAPE", "").lower() in {"1", "true", "yes"}


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

def _scrape_portal_fields() -> list[dict]:
    """Parse ticket fields from the public Freshdesk portal form.

    This is used as a fallback when the Freshdesk API cannot be queried for
    ticket field metadata. The parser extracts labels, input names, and any
    select options from the ``/support/tickets/new`` HTML page. The resulting
    structure is simplified compared to the API response but sufficient for
    building a basic question flow.
    """

    try:
        resp = requests.get(PORTAL_TICKET_FORM_URL, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        log.error("❌ Failed to fetch portal form %s (%s)", PORTAL_TICKET_FORM_URL, e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    form = soup.find("form", id="portal_ticket_form") or soup.find("form")
    if not form:
        return []

    fields: list[dict] = []
    sections_by_parent: dict[object, dict[int, dict]] = {}
    section_fields: dict[int, list] = {}

    def parse_fields(container, current_section: int | None = None):
        for label in container.find_all("label"):
            field_dom_id = label.get("for")
            if not field_dom_id:
                continue
            input_el = container.find(id=field_dom_id)
            if input_el is None:
                continue

            name = input_el.get("name") or field_dom_id
            field_type = input_el.name

            numeric_id: int | None = None
            candidates = [
                input_el.get("data-field-id"),
                input_el.get("data-fieldid"),
                input_el.get("data-id"),
                label.get("data-field-id"),
                label.get("data-fieldid"),
                label.get("data-id"),
            ]
            container_id = input_el.find_parent(attrs={"data-field-id": True})
            if container_id is None:
                container_id = label.find_parent(attrs={"data-field-id": True})
            if container_id is not None:
                candidates.append(container_id.get("data-field-id"))
            for cand in candidates:
                if cand and str(cand).isdigit():
                    try:
                        numeric_id = int(cand)
                        break
                    except (TypeError, ValueError):
                        pass
            if numeric_id is None:
                for attr in (input_el.get("id"), name):
                    digits = "".join(ch for ch in str(attr) if ch.isdigit())
                    if digits:
                        try:
                            numeric_id = int(digits)
                            break
                        except (TypeError, ValueError):
                            numeric_id = None

            choices = []
            if field_type == "select":
                for opt in input_el.find_all("option"):
                    value = opt.get("value")
                    text = opt.get_text(strip=True)
                    if value is None:
                        continue
                    choices.append({"value": value, "label": text})
            field_obj = {
                "id": numeric_id if numeric_id is not None else name,
                "label": label.get_text(strip=True),
                "type": field_type,
                "choices": choices,
            }
            if current_section is not None:
                field_obj.setdefault("section_mappings", []).append({"section_id": current_section})
                section_fields.setdefault(current_section, []).append(field_obj["id"])
            fields.append(field_obj)

            classes = input_el.get("class") or []
            input_type = (input_el.get("type") or "").lower()
            if field_type == "select" and "dynamic_sections" in classes and numeric_id is not None:
                parent_key = numeric_id
                for opt in input_el.find_all("option"):
                    sid = opt.get("data-id")
                    if not sid or not str(sid).isdigit():
                        continue
                    sid_int = int(sid)
                    sec = sections_by_parent.setdefault(parent_key, {}).setdefault(
                        sid_int, {"id": sid_int, "choices": [], "fields": []}
                    )
                    sec["choices"].append({"value": opt.get("value"), "label": opt.get_text(strip=True)})
            elif (
                field_type == "input"
                and input_type in {"radio", "checkbox"}
                and numeric_id is not None
                and any(cls in {"dynamic_sections", "depends_on"} for cls in classes)
            ):
                parent_key = numeric_id
                sid: str | None = None
                for key in ("data-dependent-id", "data-dependentid", "data-id"):
                    sid = input_el.get(key)
                    if sid and str(sid).isdigit():
                        break
                if not (sid and str(sid).isdigit()):
                    for attr, val in input_el.attrs.items():
                        if attr.startswith("data") and "id" in attr and isinstance(val, str) and val.isdigit():
                            sid = val
                            break
                if sid and str(sid).isdigit():
                    sid_int = int(sid)
                    sec = sections_by_parent.setdefault(parent_key, {}).setdefault(
                        sid_int, {"id": sid_int, "choices": [], "fields": []}
                    )
                    sec["choices"].append(
                        {"value": input_el.get("value"), "label": label.get_text(strip=True)}
                    )

        for ta in container.find_all("textarea", class_=lambda c: c and "picklist_section_" in " ".join(c)):
            cls = " ".join(ta.get("class", []))
            m = re.search(r"picklist_section_(\d+)", cls)
            if not m:
                continue
            sid = int(m.group(1))
            subsection = BeautifulSoup(ta.text, "html.parser")
            parse_fields(subsection, current_section=sid)

    parse_fields(form)

    # Some portals embed conditional field dependencies as JSON blobs inside
    # <script> tags. These map a parent field to one or more sections and the
    # child fields that should be shown when those sections are active. We
    # attempt to locate and parse these scripts so that conditional logic is
    # available even when the Freshdesk API cannot be queried.
    field_by_id = {f["id"]: f for f in fields}
    for script in soup.find_all("script"):
        text = script.string or script.get_text() or ""
        if "fieldDependencies" not in text:
            continue
        m = re.search(r"fieldDependencies\s*=\s*(\{.*?\})", text, re.S)
        if not m:
            continue
        try:
            deps = json.loads(m.group(1))
        except Exception as e:  # pragma: no cover - best effort
            log.debug("Skipping malformed fieldDependencies script: %s", e)
            continue
        if not isinstance(deps, dict):
            continue
        for parent, sec_map in deps.items():
            try:
                parent_key = int(parent)
            except (TypeError, ValueError):
                parent_key = parent
            parent_sections = sections_by_parent.setdefault(parent_key, {})
            for sec_id, children in (sec_map or {}).items():
                try:
                    sid = int(sec_id)
                except (TypeError, ValueError):
                    sid = sec_id
                sec = parent_sections.setdefault(sid, {"id": sid, "choices": [], "fields": []})
                for child in children or []:
                    try:
                        cid = int(child)
                    except (TypeError, ValueError):
                        cid = child
                    if cid not in sec["fields"]:
                        sec["fields"].append(cid)
                    field_obj = field_by_id.get(cid)
                    if field_obj is not None:
                        field_obj.setdefault("section_mappings", []).append({"section_id": sid})
                    sf = section_fields.setdefault(sid, [])
                    if cid not in sf:
                        sf.append(cid)

    for parent, sec_map in sections_by_parent.items():
        for sid, sec in sec_map.items():
            sec["fields"] = section_fields.get(sid, [])
        try:
            key = int(parent)
        except (TypeError, ValueError):
            key = parent
        _SCRAPED_SECTIONS[key] = list(sec_map.values())

    if FD_DEBUG_SCRAPE:
        for parent, secs in _SCRAPED_SECTIONS.items():
            summary = {s["id"]: s.get("fields", []) for s in secs}
            log.debug("Parsed sections parent %s -> %s", parent, summary)
        for f in fields:
            if f.get("section_mappings"):
                log.debug("Field %s section mappings: %s", f["id"], f["section_mappings"])

    return fields


# --- Cached helpers ------------------------------------------------------
_FORMS_CACHE: dict[str, object] = {"expires": 0, "data": []}
_FIELDS_CACHE: dict[str, object] = {"expires": 0, "data": []}
_SCRAPED_SECTIONS: dict[int, list] = {}

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
    if now >= _FIELDS_CACHE["expires"]:
        try:
            _FIELDS_CACHE["data"] = fd_get("/api/v2/admin/ticket_fields")
        except Exception as e:
            log.warning("Ticket fields API failed (%s); falling back to portal scrape", e)
            _FIELDS_CACHE["data"] = _scrape_portal_fields()
        _FIELDS_CACHE["expires"] = now + ttl
    return _FIELDS_CACHE["data"]


def get_form_detail(form_id: int):
    return fd_get(f"/api/v2/ticket-forms/{form_id}")


def get_sections(field_id: int):
    path = f"/api/v2/admin/ticket_fields/{field_id}/sections"
    url = f"https://{FRESHDESK_DOMAIN}.freshdesk.com{path}"
    try:
        r = _session.get(url, timeout=HTTP_TIMEOUT)
        if r.ok:
            return r.json() or []
        # Freshdesk returns 400/404/422 when a field has no conditional
        # sections configured. These responses are expected during wizard
        # traversal so we quietly treat them as "no sections" instead of
        # logging noisy errors.
        log.debug("No sections for field %s (%s)", field_id, r.status_code)
    except Exception as e:
        log.debug("No sections for field %s (%s)", field_id, e)

    # Fallback to scraped portal mappings
    secs = _SCRAPED_SECTIONS.get(int(field_id))
    if secs is None:
        _scrape_portal_fields()
        secs = _SCRAPED_SECTIONS.get(int(field_id), [])
    return secs or []


def fetch_field_detail(field_id: int) -> dict | None:
    try:
        return fd_get(f"/api/v2/admin/ticket_fields/{field_id}")
    except Exception as e:
        logging.info("No detail for field %s (%s)", field_id, e)
        return None
