from datakit.text import collapse_whitespace, slugify


def test_slugify_basic():
    assert slugify("Hello, World!") == "hello-world"


def test_slugify_keeps_digits():
    assert slugify("Release 2024 Notes") == "release-2024-notes"


def test_collapse_whitespace():
    assert collapse_whitespace("  a \n b  ") == "a b"
