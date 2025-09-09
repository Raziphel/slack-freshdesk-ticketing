from services import freshdesk


def test_get_sections_negative_cache(monkeypatch):
    calls = []

    def fake_scrape():
        calls.append('scrape')
        return []

    monkeypatch.setattr(freshdesk, '_scrape_portal_fields', fake_scrape)
    freshdesk._SCRAPED_SECTIONS.clear()

    assert freshdesk.get_sections(999999) == []
    assert freshdesk.get_sections(999999) == []
    assert calls == ['scrape']
