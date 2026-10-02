"""JSON-schema helpers for constrained decoding.

Pydantic emits `$defs` + `$ref`; several constrained-decoding backends handle nested refs poorly
(especially inside `anyOf`), so we inline them.
"""

from __future__ import annotations

import copy
from typing import Any

_DROP_KEYS = {"title"}


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    defs = schema.get("$defs", {})

    def resolve(node: Any, depth: int = 0, is_property_map: bool = False) -> Any:
        if depth > 32:
            raise ValueError("schema too deeply nested / recursive")
        if isinstance(node, dict):
            if "$ref" in node and not is_property_map:
                ref = node["$ref"]
                name = ref.rsplit("/", 1)[-1]
                target = copy.deepcopy(defs[name])
                extra = {k: v for k, v in node.items() if k != "$ref"}
                target.update(extra)
                return resolve(target, depth + 1)
            out: dict[str, Any] = {}
            for k, v in node.items():
                if k == "$defs":
                    continue
                # "title" is a schema keyword only outside a `properties` map; a field may be
                # literally named "title".
                if not is_property_map and k in _DROP_KEYS and isinstance(v, str):
                    continue
                child_is_map = k == "properties" and not is_property_map
                out[k] = resolve(v, depth + 1, is_property_map=child_is_map)
            return out
        if isinstance(node, list):
            return [resolve(v, depth + 1) for v in node]
        return node

    result = resolve(schema)
    assert isinstance(result, dict)
    return result
