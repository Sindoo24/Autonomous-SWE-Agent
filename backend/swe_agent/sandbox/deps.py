"""Discover a repository's Python dependencies by *parsing* its manifests (never executing them).

Supported (V1): PEP 621 `[project].dependencies` plus test/dev optional-dependency groups in
pyproject.toml, and requirements*.txt files (with in-repo `-r` includes). Anything that would
need code execution or arbitrary network sources (setup.py, `-e`, VCS/URL requirements, custom
indexes) is reported as unsupported instead of being honoured.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from swe_agent.core.errors import PathPolicyError
from swe_agent.repository.workspace import Workspace

TEST_EXTRAS = {"test", "tests", "testing", "dev"}
_REQ_FILE_RE = re.compile(r"^(requirements[\w.-]*\.txt|requirements/[\w.-]+\.txt)$")
MAX_REQUIREMENTS = 200


@dataclass
class DependencySpec:
    requirements: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    project_name: str | None = None
    uses_setup_py: bool = False


def _add(spec: DependencySpec, raw: str, origin: str) -> None:
    line = raw.split(" #", 1)[0].strip()
    if not line or line.startswith("#"):
        return
    try:
        req = Requirement(line)
    except InvalidRequirement:
        spec.unsupported.append(f"{origin}: {line[:120]}")
        return
    if req.url:
        spec.unsupported.append(f"{origin}: URL requirement {req.name}")
        return
    if spec.project_name and canonicalize_name(req.name) == canonicalize_name(spec.project_name):
        return
    text = str(req)
    if text not in spec.requirements and len(spec.requirements) < MAX_REQUIREMENTS:
        spec.requirements.append(text)


def _read_requirements(ws: Workspace, rel: str, spec: DependencySpec, depth: int = 0) -> None:
    if depth > 3:
        spec.unsupported.append(f"{rel}: include depth exceeded")
        return
    try:
        path = ws.jail.resolve(rel, must_exist=True)
    except PathPolicyError as exc:
        spec.unsupported.append(f"{rel}: {exc}")
        return
    spec.sources.append(rel)
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith(("-r ", "--requirement ")):
            inc = line.split(maxsplit=1)[1].strip()
            inc_rel = str(PurePosixPath(rel).parent / inc)
            _read_requirements(ws, inc_rel, spec, depth + 1)
        elif line.startswith("-"):
            # -e, -i/--index-url, -f, --extra-index-url, -c ... : never honoured
            spec.unsupported.append(f"{rel}: option ignored: {line[:80]}")
        elif "://" in line or line.startswith(("git+", "./", "../", "/")):
            spec.unsupported.append(f"{rel}: non-index requirement ignored: {line[:80]}")
        else:
            _add(spec, line, rel)


def discover_dependencies(ws: Workspace) -> DependencySpec:
    spec = DependencySpec()
    files = ws.ls_files()
    if "pyproject.toml" in files:
        try:
            data = tomllib.loads(ws.jail.resolve("pyproject.toml").read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError) as exc:
            spec.unsupported.append(f"pyproject.toml: {exc}")
            data = {}
        project = data.get("project", {}) if isinstance(data, dict) else {}
        spec.project_name = project.get("name")
        deps = project.get("dependencies", [])
        if deps:
            spec.sources.append("pyproject.toml")
        for d in deps if isinstance(deps, list) else []:
            _add(spec, str(d), "pyproject.toml")
        optional = project.get("optional-dependencies", {})
        for group, reqs in optional.items() if isinstance(optional, dict) else []:
            if group.lower() in TEST_EXTRAS and isinstance(reqs, list):
                for d in reqs:
                    _add(spec, str(d), f"pyproject.toml[{group}]")
        if data.get("tool", {}).get("poetry"):
            spec.unsupported.append("pyproject.toml: [tool.poetry] dependencies not supported (V1)")
    for rel in files:
        if _REQ_FILE_RE.match(rel):
            _read_requirements(ws, rel, spec)
    spec.uses_setup_py = "setup.py" in files
    if spec.uses_setup_py and not spec.requirements:
        spec.unsupported.append("setup.py: install_requires not read (would require executing it)")
    return spec
