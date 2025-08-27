from __future__ import annotations
import logging
from services.freshdesk import get_sections, fd_get
from logic.mapping import iter_choice_items, extract_input
from logic.mapping import ensure_choices, get_field_choices  # used by resolver
from config import FRESHDESK_EMAIL

log = logging.getLogger(__name__)

# Caching sections so I don't keep hammering the API on every branch lookup.
SECTIONS_CACHE: dict[int, list] = {}

def get_sections_cached(field_id: int):
    secs = SECTIONS_CACHE.get(field_id)
    if secs is None:
        secs = get_sections(field_id) or []
        SECTIONS_CACHE[field_id] = secs
    return secs

def activator_values(sec_obj) -> list[str]:
    src = sec_obj.get("choices") or sec_obj.get("values") or sec_obj.get("option_values") or {}
    vals = []
    for v, _lbl in iter_choice_items(src) if src else []:
        vals.append(str(v))
    return vals

# Note to self: wizard and single-page flows both lean on this; lazy import keeps cycles away.
def selected_value_for(field: dict, state_values: dict) -> str | None:
    entry = state_values.get(field.get("name")) or {}
    selected = extract_input(entry)
    if not selected:
        return None
    if isinstance(selected, str) and selected.startswith("hash:"):
        from logic.ticket import resolve_proxy_value  # lazy import to avoid cycles
        try:
            return resolve_proxy_value(field.get("name"), selected)
        except Exception:
            return selected
    return selected
