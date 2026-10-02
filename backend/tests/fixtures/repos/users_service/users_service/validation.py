from __future__ import annotations

import re

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ValidationError(Exception):
    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise ValidationError("email", "invalid email address")
    return email
