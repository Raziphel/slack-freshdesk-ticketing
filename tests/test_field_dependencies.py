import sys, pathlib
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))
from services import freshdesk
from logic.branching import activator_values

def test_section_choices_from_field_dependencies():
    deps = {"100": {"200": ["101"]}}
    choice_maps = {"100": {"200": {"p1": "Option 1"}}}
    sections_by_parent = {}
    section_fields = {}
    field_by_id = {}
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
                    sec["choices"].append({"value": val, "label": lbl})
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
    for parent, sec_map in sections_by_parent.items():
        for sid, sec in sec_map.items():
            sec["fields"] = section_fields.get(sid, [])
        freshdesk._SCRAPED_SECTIONS[int(parent)] = list(sec_map.values())
    secs = freshdesk._SCRAPED_SECTIONS.get(100)
    assert secs and activator_values(secs[0]) == ["p1"]
