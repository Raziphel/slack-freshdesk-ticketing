import types
from services import slack


def test_get_user_email_fallback(monkeypatch):
    calls = []

    def fake_api(method, payload):
        calls.append(method)
        if method == "users.info":
            return {"ok": True, "user": {"profile": {}}}
        if method == "users.profile.get":
            return {"ok": True, "profile": {"email": "u@example.com"}}
        raise AssertionError("unexpected method")

    monkeypatch.setattr(slack, "slack_api", fake_api)
    slack.get_user_email.cache_clear()
    assert slack.get_user_email("U123") == "u@example.com"
    assert calls == ["users.info", "users.profile.get"]
