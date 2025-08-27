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


# Keeping a session around so each call reuses connections and carries auth.
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


# Portal scraping

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

    # Structures populated during scraping. ``sections_by_parent`` maps a parent
    # field id to its conditional sections while ``section_fields`` collects the
    # child field ids for each section. ``name_to_id`` normalizes ``name``
    # attributes to numeric ids when available.
    name_to_id: dict[str, int] = {}
    sections_by_parent: dict[object, dict[int, dict]] = {}
    section_fields: dict[int, list] = {}
    form_key = None

    # Capture metadata from the embedded ``ticket_form`` JSON so that we can
    # preserve the question sequence and conditional logic when the API is
    # unavailable. The object is typically assigned in a ``<script>`` tag as
    # ``ticket_form = {...}``.
    m = re.search(r"ticket_form\s*=\s*(\{.*?\});", resp.text, re.S)
    if m:
        try:
            tf = json.loads(m.group(1))
            form_id = tf.get("id")
            if form_id is not None:
                try:
                    form_key = int(form_id)
                except (TypeError, ValueError):
                    form_key = form_id
                fields_json = tf.get("fields") or []
                order = [f.get("id") for f in fields_json if isinstance(f, dict) and f.get("id") is not None]
                _SCRAPED_FORM_FIELDS[form_key] = order
                for f in fields_json:
                    if not isinstance(f, dict):
                        continue
                    nm = f.get("name") or f.get("field_name")
                    fid_num = f.get("id")
                    if nm and isinstance(fid_num, int):
                        name_to_id[str(nm)] = fid_num
                deps = tf.get("field_dependencies") or {}
                choice_maps = {}
                for cvar in ("choice_field_map", "choiceFieldMap", "choice_field_maps", "choiceFieldMaps"):
                    cm = tf.get(cvar)
                    if isinstance(cm, dict):
                        choice_maps = cm
                        break
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
                        parent_choice_map = choice_maps.get(str(parent)) or choice_maps.get(parent) or {}
                        sec_choice_map = {}
                        if isinstance(parent_choice_map, dict):
                            sec_choice_map = parent_choice_map.get(str(sec_id)) or parent_choice_map.get(sec_id) or {}
                        if isinstance(sec_choice_map, dict):
                            for val, lbl in sec_choice_map.items():
                                sec["choices"].append({"value": val, "label": lbl if isinstance(lbl, str) else str(lbl)})
                        elif isinstance(sec_choice_map, list):
                            for val in sec_choice_map:
                                if isinstance(val, dict):
                                    sec["choices"].append({
                                        "value": val.get("value"),
                                        "label": val.get("label", val.get("value")),
                                    })
                                else:
                                    sec["choices"].append({"value": val, "label": str(val)})
                        elif sec_choice_map:
                            sec["choices"].append({"value": sec_choice_map, "label": str(sec_choice_map)})
                        for child in children or []:
                            try:
                                cid = int(child)
                            except (TypeError, ValueError):
                                cid = child
                            if cid not in sec["fields"]:
                                sec["fields"].append(cid)
                            sf = section_fields.setdefault(sid, [])
                            if cid not in sf:
                                sf.append(cid)
        except Exception as e:  # pragma: no cover - best effort
            log.debug("Skipping malformed ticket_form JSON: %s", e)

    soup = BeautifulSoup(resp.text, "html.parser")
    form = soup.find("form", id="portal_ticket_form") or soup.find("form")
    if not form:
        return []

    # ``ticket_form`` metadata is often embedded in the page as JSON. It maps
    # field ``name`` values to their numeric ``id`` counterparts which are
    # required for conditional section logic. Parse any such blobs up-front so
    # that scraped fields can be normalized to numeric identifiers.
    for script in soup.find_all("script"):
        text = script.string or script.get_text() or ""
        for pat in (r"portal\.ticket_form\s*=\s*(\{.*?\})", r"ticket_form\s*=\s*(\{.*?\})"):
            m = re.search(pat, text, re.S)
            if not m:
                continue
            try:
                obj = json.loads(m.group(1))
            except Exception:  # pragma: no cover - best effort
                continue
            if isinstance(obj, dict) and "ticket_form" in obj and isinstance(obj["ticket_form"], dict):
                obj = obj["ticket_form"]
            if not isinstance(obj, dict):
                continue
            fields_json = obj.get("fields") or []
            if not isinstance(fields_json, list):
                continue
            for f in fields_json:
                if not isinstance(f, dict):
                    continue
                nm = f.get("name") or f.get("field_name")
                fid_tmp = f.get("id")
                if nm and isinstance(fid_tmp, int):
                    name_to_id.setdefault(str(nm), fid_tmp)

    fields: list[dict] = []

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
            if numeric_id is None:
                numeric_id = name_to_id.get(str(name))

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
            elif field_type == "select" and numeric_id is not None:
                parent_key = numeric_id
                for opt in input_el.find_all("option"):
                    child_ids: list[int] = []
                    for attr, val in opt.attrs.items():
                        if not isinstance(attr, str) or not isinstance(val, str):
                            continue
                        if (
                            "field" in attr or "child" in attr or "dependent" in attr
                        ) and (
                            "id" in attr or "ids" in attr or "fields" in attr
                        ):
                            for d in re.findall(r"\d+", val):
                                try:
                                    child_ids.append(int(d))
                                except Exception:
                                    pass
                    if not child_ids:
                        continue
                    sid_val = None
                    for key in ("data-section-id", "data-sectionid", "data-id"):
                        sval = opt.get(key)
                        if sval and str(sval).isdigit():
                            sid_val = int(sval)
                            break
                    if sid_val is None:
                        sid_val = abs(hash((numeric_id, opt.get("value")))) % 1000000000
                    sec = sections_by_parent.setdefault(parent_key, {}).setdefault(
                        sid_val, {"id": sid_val, "choices": [], "fields": []}
                    )
                    sec["choices"].append(
                        {"value": opt.get("value"), "label": opt.get_text(strip=True)}
                    )
                    for cid in child_ids:
                        if cid not in sec["fields"]:
                            sec["fields"].append(cid)
                        sf = section_fields.setdefault(sid_val, [])
                        if cid not in sf:
                            sf.append(cid)

        for ta in container.find_all("textarea", class_=lambda c: c and "picklist_section_" in " ".join(c)):
            cls = " ".join(ta.get("class", []))
            m = re.search(r"picklist_section_(\d+)", cls)
            if not m:
                continue
            sid = int(m.group(1))
            subsection = BeautifulSoup(ta.text, "html.parser")
            parse_fields(subsection, current_section=sid)

    parse_fields(form)

    def _replace_id(val):
        if isinstance(val, str) and val in name_to_id:
            return name_to_id[val]
        return val

    for f in fields:
        f["id"] = _replace_id(f.get("id"))
    for sid, flist in section_fields.items():
        section_fields[sid] = [_replace_id(fid) for fid in flist]

    updated_sections: dict[object, dict[int, dict]] = {}
    for parent, sec_map in sections_by_parent.items():
        new_parent = _replace_id(parent)
        updated_sections[new_parent] = sec_map
    sections_by_parent = updated_sections

    # Some portals embed conditional field dependencies as JSON blobs inside
    # <script> tags. These map a parent field to one or more sections and the
    # child fields that should be shown when those sections are active. We
    # attempt to locate and parse these scripts so that conditional logic is
    # available even when the Freshdesk API cannot be queried.
    field_by_id: dict[object, dict] = {}
    for f in fields:
        fid = f.get("id")
        if fid is None:
            continue
        try:
            field_by_id[int(fid)] = f
        except (TypeError, ValueError):
            field_by_id[fid] = f
    for script in soup.find_all("script"):
        text = script.string or script.get_text() or ""
        dep_match = None
        for var in ("fieldDependencies", "dependentSections", "dependent_sections"):
            if var in text:
                dep_match = re.search(rf"{var}\s*=\s*(\{{.*?\}})", text, re.S)
                if dep_match:
                    break
        if not dep_match:
            continue
        try:
            deps = json.loads(dep_match.group(1))
        except Exception as e:  # pragma: no cover - best effort
            log.debug("Skipping malformed fieldDependencies script: %s", e)
            continue
        if not isinstance(deps, dict):
            continue
        choice_maps = {}
        for cvar in (
            "choice_field_map",
            "choiceFieldMap",
            "choice_field_maps",
            "choiceFieldMaps",
            "sectionChoiceMap",
            "section_choice_map",
        ):
            mc = re.search(rf"{cvar}\s*=\s*(\{{.*?\}})", text, re.S)
            if not mc:
                continue
            try:
                choice_maps = json.loads(mc.group(1))
            except Exception as e:  # pragma: no cover - best effort
                log.debug("Skipping malformed %s script: %s", cvar, e)
            break
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
                parent_choice_map = choice_maps.get(str(parent)) or choice_maps.get(parent)
                sec_choice_map = {}
                if isinstance(parent_choice_map, dict):
                    sec_choice_map = parent_choice_map.get(str(sec_id)) or parent_choice_map.get(sec_id) or {}
                if isinstance(sec_choice_map, dict):
                    for val, lbl in sec_choice_map.items():
                        sec["choices"].append({"value": val, "label": lbl if isinstance(lbl, str) else str(lbl)})
                elif isinstance(sec_choice_map, list):
                    for val in sec_choice_map:
                        if isinstance(val, dict):
                            sec["choices"].append({
                                "value": val.get("value"),
                                "label": val.get("label", val.get("value")),
                            })
                        else:
                            sec["choices"].append({"value": val, "label": str(val)})
                elif sec_choice_map:
                    sec["choices"].append({"value": sec_choice_map, "label": str(sec_choice_map)})
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
    for sid, flist in section_fields.items():
        for fid in flist:
            field_obj = field_by_id.get(fid)
            if field_obj is not None:
                mappings = field_obj.setdefault("section_mappings", [])
                if not any(m.get("section_id") == sid for m in mappings):
                    mappings.append({"section_id": sid})

    for parent, sec_map in sections_by_parent.items():
        for sid, sec in sec_map.items():
            sec["fields"] = section_fields.get(sid, [])
        try:
            key = int(parent)
        except (TypeError, ValueError):
            key = parent
        _SCRAPED_SECTIONS[key] = list(sec_map.values())
        if form_key is not None:
            try:
                fid_key = int(form_key)
            except (TypeError, ValueError):
                fid_key = form_key
            _SCRAPED_FORM_SECTIONS.setdefault(fid_key, {})[key] = list(sec_map.values())

    target_parent = 154001624274
    target_child = 154001624387
    tgt = sections_by_parent.get(target_parent)
    if tgt:
        found = any(target_child in (s.get("fields") or []) for s in tgt.values())
        log.info(
            "Scraped sections for %s include child %s: %s", target_parent, target_child, found
        )
        if FD_DEBUG_SCRAPE:
            log.debug("sections_by_parent[%s] -> %s", target_parent, tgt)
    else:
        log.info("Parent %s not found in scraped sections", target_parent)

    if FD_DEBUG_SCRAPE:
        for parent, secs in _SCRAPED_SECTIONS.items():
            summary = {s["id"]: s.get("fields", []) for s in secs}
            log.debug("Parsed sections parent %s -> %s", parent, summary)
        for f in fields:
            if f.get("section_mappings"):
                log.debug("Field %s section mappings: %s", f["id"], f["section_mappings"])

    return fields


# Cached helpers 
_FORMS_CACHE: dict[str, object] = {"expires": 0, "data": []}
_FIELDS_CACHE: dict[str, object] = {"expires": 0, "data": []}
_SCRAPED_SECTIONS: dict[int, list] = {}
# ``_SCRAPED_FORM_FIELDS`` stores the ordered list of field IDs for a given
# form while ``_SCRAPED_FORM_SECTIONS`` maps a form id to its conditional
# sections grouped by parent field id. These structures are populated by the
# portal scraper and act as fallbacks when the Freshdesk admin API is
# unavailable.
_SCRAPED_FORM_FIELDS: dict[int, list] = {}
_SCRAPED_FORM_SECTIONS: dict[int, dict[int, list]] = {}

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


def get_form_fields_scraped(form_id: int) -> list:
    """Return the field order scraped from the portal form."""
    order = _SCRAPED_FORM_FIELDS.get(int(form_id))
    if order is None:
        _scrape_portal_fields()
        order = _SCRAPED_FORM_FIELDS.get(int(form_id), [])
    return order or []


def get_sections_scraped(form_id: int) -> dict[int, list]:
    """Return conditional sections scraped for ``form_id``.

    The returned mapping is ``{parent_field_id: [section, ...]}`` where each
    section object mirrors the structure returned by the Freshdesk admin API.
    """
    secs = _SCRAPED_FORM_SECTIONS.get(int(form_id))
    if secs is None:
        _scrape_portal_fields()
        secs = _SCRAPED_FORM_SECTIONS.get(int(form_id), {})
    return dict(secs or {})


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
