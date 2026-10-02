from datakit.text import slugify


def test_digits_kept():
    assert slugify("Python 3.12 release") == "python-3-12-release"


def test_only_digits():
    assert slugify("2024") == "2024"


def test_letters_still_normalised():
    assert slugify("Crème Brûlée!") == "creme-brulee"
