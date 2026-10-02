"""tree-sitter symbol index for Python.

Why tree-sitter rather than `ast`: it is error-tolerant (still indexes a file that is mid-edit or
has a syntax error) and gives a path to other languages later. `ast` is used separately where
exactness matters (patch syntax validation).

The index is name-based: it knows definitions and where identifiers appear, not types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import tree_sitter_python
from pydantic import BaseModel
from tree_sitter import Language, Node, Parser

PY_LANGUAGE = Language(tree_sitter_python.language())

MAX_INDEX_FILE_BYTES = 1_000_000


class SymbolDef(BaseModel):
    name: str
    qualname: str
    kind: str  # "function" | "method" | "class"
    path: str
    start_line: int
    end_line: int
    signature: str
    decorators: list[str] = []


class ImportRef(BaseModel):
    path: str
    line: int
    text: str


@dataclass
class FileIndex:
    path: str
    symbols: list[SymbolDef] = field(default_factory=list)
    imports: list[ImportRef] = field(default_factory=list)
    has_errors: bool = False
    line_count: int = 0


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _signature(node: Node, src: bytes) -> str:
    """Header line(s) of a def/class up to the colon, whitespace-collapsed."""
    body = node.child_by_field_name("body")
    end = body.start_byte if body is not None else node.end_byte
    header = src[node.start_byte : end].decode("utf-8", errors="replace").rstrip()
    header = header.rstrip(":").rstrip()
    return " ".join(header.split())[:240]


def index_source(path: str, src: bytes) -> FileIndex:
    parser = Parser(PY_LANGUAGE)
    tree = parser.parse(src)
    fi = FileIndex(path=path, has_errors=tree.root_node.has_error, line_count=src.count(b"\n") + 1)

    def walk(node: Node, scope: list[tuple[str, str]], decorators: list[str]) -> None:
        for child in node.children:
            if child.type == "decorated_definition":
                decs = [_text(d, src).strip() for d in child.children if d.type == "decorator"]
                inner = child.child_by_field_name("definition")
                if inner is not None:
                    handle(inner, scope, decs, outer=child)
                continue
            if child.type in ("function_definition", "class_definition"):
                handle(child, scope, [], outer=child)
                continue
            if child.type in ("import_statement", "import_from_statement") and not scope:
                fi.imports.append(
                    ImportRef(path=path, line=child.start_point[0] + 1, text=_text(child, src))
                )
                continue
            # Recurse into compound statements (if/try/with at module level) but not expressions.
            if child.type in (
                "if_statement",
                "try_statement",
                "with_statement",
                "block",
                "else_clause",
                "except_clause",
                "finally_clause",
                "elif_clause",
            ):
                walk(child, scope, [])

    def handle(node: Node, scope: list[tuple[str, str]], decs: list[str], outer: Node) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = _text(name_node, src)
        is_class = node.type == "class_definition"
        in_class = bool(scope) and scope[-1][1] == "class"
        kind = "class" if is_class else ("method" if in_class else "function")
        qual = ".".join([s for s, _ in scope] + [name])
        fi.symbols.append(
            SymbolDef(
                name=name,
                qualname=qual,
                kind=kind,
                path=path,
                start_line=outer.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                signature=_signature(node, src),
                decorators=decs,
            )
        )
        body = node.child_by_field_name("body")
        if body is not None:
            walk(body, [*scope, (name, "class" if is_class else "function")], [])

    walk(tree.root_node, [], [])
    return fi


class SymbolIndex:
    def __init__(self) -> None:
        self.files: dict[str, FileIndex] = {}

    @classmethod
    def build(cls, root: Path, rel_paths: list[str]) -> SymbolIndex:
        idx = cls()
        for rel in rel_paths:
            if not rel.endswith(".py"):
                continue
            p = root / rel
            try:
                if p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_INDEX_FILE_BYTES:
                    continue
                idx.files[rel] = index_source(rel, p.read_bytes())
            except OSError:
                continue
        return idx

    def update_file(self, root: Path, rel: str) -> None:
        p = root / rel
        if rel.endswith(".py") and p.is_file():
            self.files[rel] = index_source(rel, p.read_bytes())

    def all_symbols(self) -> list[SymbolDef]:
        return [s for f in self.files.values() for s in f.symbols]

    def find(self, name: str, kind: str | None = None) -> list[SymbolDef]:
        """Match by simple name, or by dotted qualname suffix (e.g. `UserService.create`)."""
        out = []
        for s in self.all_symbols():
            if kind and s.kind != kind:
                continue
            if s.name == name or s.qualname == name or s.qualname.endswith("." + name):
                out.append(s)
        return out

    def outline(self, rel: str) -> FileIndex | None:
        return self.files.get(rel)

    def enclosing(self, rel: str, line: int) -> SymbolDef | None:
        fi = self.files.get(rel)
        if fi is None:
            return None
        best: SymbolDef | None = None
        for s in fi.symbols:
            if s.start_line <= line <= s.end_line and (
                best is None or s.start_line >= best.start_line
            ):
                best = s
        return best
