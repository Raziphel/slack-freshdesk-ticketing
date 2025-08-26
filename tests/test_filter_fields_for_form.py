import sys, pathlib
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

from logic import wizard


def test_filter_fields_for_form_uses_form_detail(monkeypatch):
    form = {"id": 5}
    fd_fields = [{"id": 1}, {"id": 2}]
    monkeypatch.setattr(wizard, "get_form_detail", lambda fid: {"fields": [1]})
    monkeypatch.setattr(wizard, "get_form_fields_scraped", lambda fid: (_ for _ in ()).throw(Exception("should not call")))
    monkeypatch.setattr(wizard, "get_sections_cached", lambda fid: [])
    filtered = wizard.filter_fields_for_form(form, fd_fields)
    assert [f["id"] for f in filtered] == [1]


def test_filter_fields_for_form_scraped_on_failure(monkeypatch):
    form = {"id": 5}
    fd_fields = [{"id": 1}, {"id": 2}, {"id": 3}]
    monkeypatch.setattr(wizard, "get_form_detail", lambda fid: (_ for _ in ()).throw(Exception("boom")))
    monkeypatch.setattr(wizard, "get_form_fields_scraped", lambda fid: [2])
    monkeypatch.setattr(wizard, "get_sections_cached", lambda fid: [])
    filtered = wizard.filter_fields_for_form(form, fd_fields)
    assert [f["id"] for f in filtered] == [2]


def test_filter_fields_for_form_no_ids_returns_all(monkeypatch):
    form = {"id": 5}
    fd_fields = [{"id": 1}, {"id": 2}]
    monkeypatch.setattr(wizard, "get_form_detail", lambda fid: {})
    monkeypatch.setattr(wizard, "get_form_fields_scraped", lambda fid: [])
    monkeypatch.setattr(wizard, "get_sections_cached", lambda fid: (_ for _ in ()).throw(Exception("should not call")))
    filtered = wizard.filter_fields_for_form(form, fd_fields)
    assert filtered == fd_fields
