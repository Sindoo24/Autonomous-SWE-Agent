from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class User:
    id: int
    name: str
    email: str


@dataclass
class UserStore:
    users: dict[int, User] = field(default_factory=dict)
    next_id: int = 1

    def add(self, name: str, email: str) -> User:
        user = User(id=self.next_id, name=name, email=email)
        self.users[user.id] = user
        self.next_id += 1
        return user

    def get(self, user_id: int) -> User | None:
        return self.users.get(user_id)
