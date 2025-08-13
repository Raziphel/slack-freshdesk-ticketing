import logging, os
from config import PORTAL_FORMS_ORDER, ALLOWED_FORM_IDS
from logic.mapping import slug

log = logging.getLogger(__name__)

def filter_portal_forms(forms: list[dict]):
    log.info("FD forms available: %s", [f.get("name") for f in forms])

    if ALLOWED_FORM_IDS:
        by_id = {str(f.get("id")): f for f in forms}
        filtered = [by_id[i] for i in ALLOWED_FORM_IDS if i in by_id]
        if filtered:
            log.info("Using ALLOWED_FORM_IDS: %s", ALLOWED_FORM_IDS)
            return filtered
        log.warning("No matching forms for ALLOWED_FORM_IDS=%s; falling back to all", ALLOWED_FORM_IDS)
        return forms

    targets = [slug(n) for n in PORTAL_FORMS_ORDER if n and n.strip()]
    by_slug = {slug(f.get("name") or ""): f for f in forms}
    exact = [by_slug[s] for s in targets if s in by_slug]
    if exact:
        return exact

    pool = [(slug(f.get("name") or ""), f) for f in forms]
    fuzzy, seen = [], set()
    for t in targets:
        pick = next((f for sl, f in pool if t and sl and t in sl and f.get("id") not in seen), None)
        if pick:
            fuzzy.append(pick); seen.add(pick.get("id"))
    if fuzzy:
        log.warning("Using fuzzy form matches: %s", [f.get("name") for f in fuzzy])
        return fuzzy

    log.warning("No matching forms found for configured names; falling back to all forms")
    return forms

def normalize_id_list(raw_ids):
    ids = []
    for f in raw_ids or []:
        if isinstance(f, dict):
            if "id" in f:
                ids.append(f["id"])
        else:
            ids.append(f)
    return ids
