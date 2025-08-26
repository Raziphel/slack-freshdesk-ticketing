import sys, pathlib
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

from logic import wizard


def _fields():
    return [
        {
            "id": 100,
            "type": "nested_field",
            "required_for_customers": True,
            "dependent_fields": [
                {"id": 10, "name": "cat", "type": "custom_text", "required_for_customers": True},
                {"id": 11, "name": "app", "type": "custom_text", "required_for_customers": True},
            ],
        }
    ]


def test_compute_pages_linearizes_nested_fields(monkeypatch):
    monkeypatch.setattr(wizard, "get_form_detail", lambda fid: {"fields": [100]})
    form = {"id": 1, "fields": [100]}
    fields = _fields()
    pages = wizard.compute_pages(form, fields, {})
    assert pages == [10, "core", None]

    state = {"cat": {"c": {"type": "plain_text_input", "value": "x"}}}
    pages = wizard.compute_pages(form, fields, state)
    assert pages == [10, 11, "core", None]


def test_build_fields_for_page_renders_single_child():
    form = {"id": 1}
    fields = _fields()
    blocks = wizard.build_fields_for_page(form, fields, {}, 10)
    assert blocks and blocks[0].get("block_id") == "cat"
    blocks2 = wizard.build_fields_for_page(form, fields, {}, 11)
    assert blocks2 and blocks2[0].get("block_id") == "app"
