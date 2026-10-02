"""Parse pytest JUnit XML (junit_family=xunit1, which carries `file` attributes).

The XML is written by a process running untrusted code, so it is size-capped before parsing and
only ElementTree is used (no external entity resolution; expat >= 2.4 limits entity expansion).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from swe_agent.schemas.state import TestCaseFailure

MAX_JUNIT_BYTES = 5_000_000
MAX_FAILURE_DETAILS = 8
EXCERPT_CHARS = 1500


@dataclass
class JUnitResult:
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: int = 0
    collection_errors: list[str] = field(default_factory=list)
    failures: list[TestCaseFailure] = field(default_factory=list)


def _nodeid(case: ET.Element) -> str:
    file = case.get("file") or ""
    classname = case.get("classname") or ""
    name = case.get("name") or ""
    if not file:
        return f"{classname}::{name}" if classname else name
    module = file[:-3].replace("/", ".") if file.endswith(".py") else file
    rest = classname[len(module) :].lstrip(".") if classname.startswith(module) else ""
    parts = [file, *([p for p in rest.split(".") if p]), name]
    return "::".join(parts)


def parse_junit(xml_text: str) -> JUnitResult:
    if len(xml_text) > MAX_JUNIT_BYTES:
        raise ValueError("junit report too large")
    root = ET.fromstring(xml_text)  # noqa: S314 - size-capped; ElementTree resolves no external entities
    res = JUnitResult()
    for case in root.iter("testcase"):
        failure = case.find("failure")
        error = case.find("error")
        skipped = case.find("skipped")
        if error is not None and "collection failure" in (error.get("message") or ""):
            name = case.get("name") or ""
            path = case.get("file") or (name.replace(".", "/") + ".py" if name else "?")
            res.collection_errors.append(path)
            _detail(res, path, "error", error)
            continue
        nid = _nodeid(case)
        if failure is not None:
            res.failed.append(nid)
            _detail(res, nid, "failure", failure)
        elif error is not None:
            res.errors.append(nid)
            _detail(res, nid, "error", error)
        elif skipped is not None:
            res.skipped += 1
        else:
            res.passed.append(nid)
    return res


def _detail(res: JUnitResult, nid: str, kind: str, el: ET.Element) -> None:
    if len(res.failures) >= MAX_FAILURE_DETAILS:
        return
    text = (el.text or "").strip()
    res.failures.append(
        TestCaseFailure(
            nodeid=nid,
            kind="failure" if kind == "failure" else "error",
            message=(el.get("message") or "")[:500],
            excerpt=text[-EXCERPT_CHARS:],
        )
    )
