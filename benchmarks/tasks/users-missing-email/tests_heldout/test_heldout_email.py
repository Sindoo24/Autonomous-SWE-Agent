import pytest

import users_service.api as api
from users_service.store import UserStore


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch):
    monkeypatch.setattr(api, "store", UserStore())


def test_missing_email_returns_422():
    status, body = api.post_users({"name": "Ada"})
    assert status == 422
    assert body["field"] == "email"


def test_empty_email_returns_422():
    status, body = api.post_users({"name": "Ada", "email": "   "})
    assert status == 422
    assert body["field"] == "email"


def test_valid_user_still_created():
    status, body = api.post_users({"name": "Ada", "email": "ada@example.com"})
    assert status == 201 and body["email"] == "ada@example.com"
