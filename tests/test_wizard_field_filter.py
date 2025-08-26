import sys
import pathlib

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

from logic import wizard


def test_filter_fields_for_form_collects_children(monkeypatch):
    form = {"id": 10, "fields": [1]}
    fd_fields = [
        {"id": 1, "name": "p1"},
        {"id": 2, "name": "c1"},
        {"id": 3, "name": "gc"},
        {"id": 4, "name": "other"},
        {"id": 5, "type": "default_subject", "name": "subj"},
        {"id": 6, "type": "default_description", "name": "desc"},
    ]

    def fake_get_sections(fid):
        if fid == 1:
            return [{"id": 11, "fields": [2]}]
        if fid == 2:
            return [{"id": 22, "fields": [3]}]
        return []

    monkeypatch.setattr(wizard, "get_sections_cached", fake_get_sections)

    filtered = wizard.filter_fields_for_form(form, fd_fields)
    ids = {f["id"] for f in filtered}
    assert ids == {1, 2, 3, 5, 6}


def test_open_wizard_first_page_uses_filtered_fields(monkeypatch):
    wizard.WIZARD_SESSIONS.clear()
    form = {"id": 1, "fields": [1]}
    fd_fields = [{"id": 1}, {"id": 2}]

    monkeypatch.setattr(wizard, "get_ticket_forms_cached", lambda: [form])
    monkeypatch.setattr(wizard, "get_ticket_fields_cached", lambda: fd_fields)

    captured = {}

    def fake_filter(f, fields):
        captured["called_with"] = (f, fields)
        return [{"id": 1}]

    monkeypatch.setattr(wizard, "filter_fields_for_form", fake_filter)

    def fake_build(form_, fields_arg, token, page, state):
        captured["fields_passed"] = fields_arg
        return {"type": "modal"}

    monkeypatch.setattr(wizard, "build_wizard_page_modal", fake_build)
    monkeypatch.setattr(wizard, "slack_api", lambda *a, **k: None)

    wizard.open_wizard_first_page("vid", 1, None)

    assert captured["called_with"] == (form, fd_fields)
    assert captured["fields_passed"] == [{"id": 1}]


def test_update_wizard_uses_filtered_fields(monkeypatch):
    wizard.WIZARD_SESSIONS.clear()
    token = "tok"
    wizard.WIZARD_SESSIONS[token] = {"ticket_form_id": 1, "page": 0, "values": {}}
    form = {"id": 1, "fields": [1]}
    fd_fields = [{"id": 1}, {"id": 2}]

    monkeypatch.setattr(wizard, "get_ticket_forms_cached", lambda: [form])
    monkeypatch.setattr(wizard, "get_ticket_fields_cached", lambda: fd_fields)

    captured = {"compute_fields": []}

    def fake_filter(f, fields):
        captured["called"] = (f, fields)
        return [{"id": 1}]

    monkeypatch.setattr(wizard, "filter_fields_for_form", fake_filter)

    def fake_compute(form_, fields_arg, state):
        captured["compute_fields"].append(fields_arg)
        return [None]

    monkeypatch.setattr(wizard, "compute_pages", fake_compute)

    def fake_build(form_, fields_arg, tok, page, state):
        captured["fields_passed"] = fields_arg
        return {"type": "modal"}

    monkeypatch.setattr(wizard, "build_wizard_page_modal", fake_build)
    monkeypatch.setattr(wizard, "slack_api", lambda *a, **k: None)

    wizard.update_wizard("vid", token, None, None)

    assert captured["called"] == (form, fd_fields)
    # compute_pages is invoked twice before rendering
    assert captured["compute_fields"] == [[{"id": 1}], [{"id": 1}]]
    assert captured["fields_passed"] == [{"id": 1}]
