from __future__ import annotations
import re, hashlib, logging
from config import MAX_BLOCKS
from services.freshdesk import fetch_field_detail

log = logging.getLogger(__name__)

# Type families
TEXT_LIKE = {"text","email","phone_number","short_text","long_text","custom_text","custom_url","url"}
NUMBER_LIKE = {"custom_number","custom_decimal","number","decimal"}
PARAGRAPH_LIKE = {"custom_paragraph","textarea"}
DATE_LIKE = {"custom_date","date"}
CHECKBOX_LIKE = {"custom_checkbox","checkbox","boolean"}
DROPDOWN_LIKE = {"custom_dropdown","dropdown","default_ticket_type"}
NESTED = {"nested_field"}

SKIP_ALWAYS = {
    "default_requester","default_source","default_status","default_priority",
    "default_group","default_agent","default_product","default_company",
}

def slug(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("&", "and")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", "-", s)

def proxy_value_if_needed(raw: str) -> str:
    raw = str(raw)
    if len(raw) <= 150:
        return raw
    return "hash:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()

def iter_choice_items(choices):
    if not choices:
        return
    if isinstance(choices, dict):
        for v, l in choices.items():
            yield (v, l)
    elif isinstance(choices, list):
        for item in choices:
            if isinstance(item, dict):
                val = item.get("value", item.get("name", item.get("id", "")))
                lbl = item.get("label", item.get("name", val))
                if val is None:
                    val = lbl
                yield (str(val), str(lbl))
            else:
                yield (str(item), str(item))
    else:
        yield (str(choices), str(choices))

def get_field_choices(field: dict):
    cp = field.get("customers_properties") or {}
    for k in ("choices","option_values","values"):
        if cp.get(k):
            return cp.get(k)
    for k in ("choices","option_values","values","dropdown_choices","label_choices"):
        if field.get(k):
            return field.get(k)
    pp = field.get("portal_properties") or {}
    for k in ("choices","option_values","values"):
        if pp.get(k):
            return pp.get(k)
    return None

def ensure_choices(field: dict) -> dict:
    if field.get("type") in DROPDOWN_LIKE and not get_field_choices(field):
        detail = fetch_field_detail(field["id"])
        if detail and isinstance(detail, dict):
            if detail.get("customers_properties"):
                field.setdefault("customers_properties", {}).update(detail["customers_properties"])
            for k in ("choices","option_values","values","dropdown_choices","label_choices","portal_properties"):
                if detail.get(k):
                    field[k] = detail[k]
    return field

def choices_to_slack_options(choices, field: dict | None = None):
    options = []
    for val, lbl in iter_choice_items(choices):
        visible = str(val) if str(val).strip() else str(lbl)
        options.append({
            "text":  {"type": "plain_text", "text": visible[:75]},
            "value": proxy_value_if_needed(val)
        })
    return options

def to_slack_block(field: dict):
    ftype = field.get("type")
    name  = field.get("name")
    label = field.get("label_for_customers") or field.get("label") or name or "Field"
    required = bool(field.get("required_for_customers"))
    displayed = bool(field.get("displayed_to_customers"))
    customers_can_edit = bool(field.get("customers_can_edit", True))

    if ftype in SKIP_ALWAYS:
        return None
    if not displayed and not required:
        return None
    if not customers_can_edit and not required:
        return None

    if ftype == "default_subject":
        return {"type":"input","block_id":"subject","label":{"type":"plain_text","text":"Subject"},
                "element":{"type":"plain_text_input","action_id":"subject"},
                "optional":False,"dispatch_action":True}
    if ftype == "default_description":
        return {"type":"input","block_id":"description","label":{"type":"plain_text","text":"Describe the issue"},
                "element":{"type":"plain_text_input","action_id":"description","multiline":True},
                "optional":False,"dispatch_action":True}

    if ftype in TEXT_LIKE:
        elem = {"type":"plain_text_input","action_id":name}
    elif ftype in NUMBER_LIKE:
        elem = {"type":"plain_text_input","action_id":name}
        label = f"{label} (number)"
    elif ftype in PARAGRAPH_LIKE:
        elem = {"type":"plain_text_input","action_id":name,"multiline":True}
    elif ftype in DATE_LIKE:
        elem = {"type":"datepicker","action_id":name}
    elif ftype in CHECKBOX_LIKE:
        elem = {"type":"checkboxes","action_id":name,
                "options":[{"text":{"type":"plain_text","text":label},"value":"true"}]}
    elif ftype in DROPDOWN_LIKE:
        raw_choices = get_field_choices(field)
        options = choices_to_slack_options(raw_choices, field)
        if options:
            elem = {"type":"static_select","action_id":name,
                    "placeholder":{"type":"plain_text","text":"Select..."},"options":options}
        else:
            elem = {"type":"plain_text_input","action_id":name}
            label = f"{label} (enter value)"
    elif ftype in NESTED:
        dep = field.get("dependent_fields") or []
        blocks = []
        for df in sorted(dep, key=lambda d: d.get("level", 99)):
            dname = df.get("name")
            dlabel = df.get("label_for_customers") or df.get("label") or dname
            blocks.append({"type":"input","block_id":dname,
                           "label":{"type":"plain_text","text":dlabel},
                           "element":{"type":"plain_text_input","action_id":dname},
                           "optional":not required,"dispatch_action":True})
        return blocks
    else:
        log.info("Skipping unsupported type: %s (%s)", ftype, name)
        return None

    return {"type":"input","block_id":name,"label":{"type":"plain_text","text":label},
            "element":elem,"optional":not required,"dispatch_action":True}

def normalize_blocks(mapped):
    if not mapped:
        return []
    return mapped if isinstance(mapped, list) else [mapped]

def extract_input(entry: dict):
    if not entry:
        return None
    action_id, data = next(iter(entry.items()))
    t = data.get("type")
    if t == "plain_text_input":
        return data.get("value")
    if t == "static_select":
        sel = data.get("selected_option")
        return sel.get("value") if sel else None
    if t == "datepicker":
        return data.get("selected_date")
    if t == "checkboxes":
        return bool(data.get("selected_options"))
    return None
