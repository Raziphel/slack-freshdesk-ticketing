import sys, pathlib, logging
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

from services import freshdesk
from logic import wizard


def test_scrape_portal_parses_ticket_form_json(monkeypatch):
    html = '''
    <html><body>
    <script>
    ticket_form = {
        "id": 5,
        "fields": [
            {"id": 1, "name": "parent"},
            {"id": 2, "name": "child"}
        ],
        "field_dependencies": {"1": {"10": [2]}},
        "choice_field_map": {"1": {"10": {"opt1": "Option 1"}}}
    };
    </script>
    <form id="portal_ticket_form">
        <label for="f1">Parent</label><input id="f1" name="parent" />
        <label for="f2">Child</label><input id="f2" name="child" />
    </form>
    </body></html>
    '''
    class Resp:
        text = html
        def raise_for_status(self):
            pass
    monkeypatch.setattr(freshdesk.requests, "get", lambda url, timeout: Resp())
    freshdesk._SCRAPED_FORM_FIELDS.clear()
    freshdesk._SCRAPED_FORM_SECTIONS.clear()
    freshdesk._SCRAPED_SECTIONS.clear()
    freshdesk._scrape_portal_fields()
    assert freshdesk.get_form_fields_scraped(5) == [1, 2]
    secs = freshdesk.get_sections_scraped(5)
    assert 1 in secs
    assert secs[1][0]["fields"] == [2]
    assert secs[1][0]["choices"][0]["value"] == "opt1"


def test_compute_pages_uses_scraped_sections_on_api_failure(monkeypatch):
    freshdesk._SCRAPED_FORM_FIELDS.clear()
    freshdesk._SCRAPED_FORM_SECTIONS.clear()
    freshdesk._SCRAPED_FORM_FIELDS[5] = [1]
    freshdesk._SCRAPED_FORM_SECTIONS[5] = {
        1: [{"id": 10, "choices": [{"value": "v1", "label": "V1"}], "fields": [2]}]
    }
    form = {"id": 5}
    fields = [
        {
            "id": 1,
            "name": "parent",
            "type": "custom_dropdown",
            "choices": [{"value": "v1", "label": "V1"}],
            "required_for_customers": True,
        },
        {
            "id": 2,
            "name": "child",
            "type": "custom_text",
            "required_for_customers": True,
        },
    ]
    state_values = {"parent": {"a": {"type": "static_select", "selected_option": {"value": "v1"}}}}
    monkeypatch.setattr(wizard, "get_form_detail", lambda fid: (_ for _ in ()).throw(Exception("boom")))
    monkeypatch.setattr(wizard, "get_sections_cached", lambda fid: (_ for _ in ()).throw(Exception("should not call")))
    pages = wizard.compute_pages(form, fields, state_values)
    assert pages[:2] == [1, 2]


def test_scrape_alt_dom_pattern_and_compute_pages(monkeypatch, caplog):
    html = '''
    <html><body>
    <script>
    ticket_form = {
        "id": 5,
        "fields": [
            {"id": 154001624274, "name": "parent"},
            {"id": 154001624387, "name": "child"}
        ]
    };
    </script>
    <form id="portal_ticket_form">
        <label for="p">Parent</label>
        <select id="p" name="parent">
            <option value="" />
            <option value="other" data-section-id="77" data-dependent-fields="[154001624387]">Other SaaS Applications</option>
        </select>
        <label for="c">Child</label><input id="c" name="child" />
    </form>
    </body></html>
    '''

    class Resp:
        text = html
        def raise_for_status(self):
            pass

    monkeypatch.setattr(freshdesk.requests, "get", lambda url, timeout: Resp())
    freshdesk._SCRAPED_FORM_FIELDS.clear()
    freshdesk._SCRAPED_FORM_SECTIONS.clear()
    freshdesk._SCRAPED_SECTIONS.clear()
    with caplog.at_level(logging.INFO):
        freshdesk._scrape_portal_fields()
    assert "154001624274" in caplog.text
    assert "154001624387" in caplog.text
    secs = freshdesk.get_sections_scraped(5)
    assert 154001624274 in secs
    assert any(154001624387 in s.get("fields", []) for s in secs[154001624274])

    form = {"id": 5}
    fields = [
        {
            "id": 154001624274,
            "name": "parent",
            "type": "custom_dropdown",
            "choices": [{"value": "other", "label": "Other SaaS Applications"}],
            "required_for_customers": True,
        },
        {
            "id": 154001624387,
            "name": "child",
            "type": "custom_text",
            "required_for_customers": True,
        },
    ]
    state_values = {"parent": {"a": {"type": "static_select", "selected_option": {"value": "other"}}}}
    monkeypatch.setattr(wizard, "get_form_detail", lambda fid: (_ for _ in ()).throw(Exception("boom")))
    monkeypatch.setattr(wizard, "get_sections_cached", lambda fid: (_ for _ in ()).throw(Exception("should not call")))
    pages = wizard.compute_pages(form, fields, state_values)
    assert pages[:2] == [154001624274, 154001624387]
