"""Filesystem artifact store.

Large payloads (diffs, prompts, model responses, tool outputs) are written here and referenced
by id from graph state, the trajectory and (later) the database. Keeps checkpoints small.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel

_ART_RE = re.compile(r"^art_(\d+)_([A-Za-z0-9_]+?)(\.[A-Za-z0-9]+)?$")


class ArtifactRef(BaseModel):
    id: str
    kind: str
    path: str
    sha256: str
    bytes: int


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        self._lock = threading.Lock()
        self.index: dict[str, ArtifactRef] = {}
        self._load_existing()

    def _load_existing(self) -> None:
        """Re-open a run's artifact directory (resume after approval / restart): keep numbering
        monotonic and make earlier artifacts readable by id."""
        for path in self.root.glob("art_*"):
            m = _ART_RE.match(path.name)
            if not m or not path.is_file():
                continue
            art_id = f"art_{m.group(1)}_{m.group(2)}"
            self._seq = max(self._seq, int(m.group(1)))
            data = path.read_bytes()
            self.index[art_id] = ArtifactRef(
                id=art_id, kind=m.group(2), path=str(path),
                sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
            )  # fmt: skip

    def put_text(self, kind: str, text: str, suffix: str = ".txt") -> ArtifactRef:
        data = text.encode("utf-8", errors="replace")
        digest = hashlib.sha256(data).hexdigest()
        with self._lock:
            self._seq += 1
            art_id = f"art_{self._seq:05d}_{kind}"
        path = self.root / f"{art_id}{suffix}"
        path.write_bytes(data)
        ref = ArtifactRef(id=art_id, kind=kind, path=str(path), sha256=digest, bytes=len(data))
        self.index[art_id] = ref
        return ref

    def put_json(self, kind: str, obj: Any) -> ArtifactRef:
        return self.put_text(kind, json.dumps(obj, indent=2, default=str), suffix=".json")

    def read_text(self, art_id: str) -> str:
        return Path(self.index[art_id].path).read_text(encoding="utf-8")
