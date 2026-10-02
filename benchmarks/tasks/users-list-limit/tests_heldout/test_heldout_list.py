import pytest

import users_service.api as api
from users_service.store import UserStore


@pytest.fixture(autouse=True)
def three_users(monkeypatch):
    store = UserStore()
    for name in ("a", "b", "c"):
        store.add(name=name, email=f"{name}@example.com")
    monkeypatch.setattr(api, "store", store)


def test_limit_is_respected():
    status, body = api.list_users(limit=2)
    assert status == 200
    assert [u["name"] for u in body["items"]] == ["a", "b"]


def test_offset_and_remaining():
    _, body = api.list_users(offset=1, limit=10)
    assert [u["name"] for u in body["items"]] == ["b", "c"]
    assert body["total"] == 3


def test_limit_one():
    _, body = api.list_users(offset=2, limit=1)
    assert [u["name"] for u in body["items"]] == ["c"]
