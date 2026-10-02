from users_service.api import get_user, list_users, post_users


def test_create_user():
    status, body = post_users({"name": "Ada", "email": "ADA@example.com "})
    assert status == 201
    assert body["email"] == "ada@example.com"


def test_missing_name_is_422():
    status, body = post_users({"email": "x@example.com"})
    assert status == 422
    assert body["field"] == "name"


def test_get_missing_user_is_404():
    assert get_user(999)[0] == 404


def test_list_users_rejects_bad_limit():
    assert list_users(limit=0)[0] == 422
    assert list_users(limit=101)[0] == 422
