"""Sandbox images.

Two layers, both built with `docker build --network none`:

1. base image  = SANDBOX_BASE_IMAGE + a venv at /opt/venv + an unprivileged user (uid 10001).
2. deps image  = base + the repository's dependencies, installed from a local wheelhouse.

The wheelhouse is filled on the host with `pip download --only-binary=:all:` for the sandbox's
Python version and platform. Wheels are installed by unpacking, so no package code runs on the
host, and the image build needs no network. Dependencies that only ship source distributions are
reported as an environment-setup failure (V1 limitation) instead of running their build scripts.

Images are tagged by a content hash, so repeated runs on the same dependencies reuse the cache.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from swe_agent.config import SandboxSettings
from swe_agent.sandbox.docker import EnvSetupError, SandboxError, docker, image_exists

BASE_DOCKERFILE = """\
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
RUN python3 -m venv /opt/venv \\
 && (getent group 10001 >/dev/null || groupadd -g 10001 sandbox) \\
 && (getent passwd 10001 >/dev/null \\
     || useradd -u 10001 -g 10001 -M -d /tmp -s /usr/sbin/nologin sandbox) \\
 && mkdir -p /workspace && chmod 0777 /workspace
ENV PATH=/opt/venv/bin:$PATH \\
    PYTHONDONTWRITEBYTECODE=1
LABEL swe-agent.image=base
WORKDIR /workspace
"""

DEPS_DOCKERFILE = """\
ARG BASE
FROM ${BASE}
COPY wheelhouse /tmp/wheelhouse
# pip settings apply to this build step only: pip never runs inside a sandbox at test time, so
# they are kept out of the runtime environment.
RUN PIP_NO_INPUT=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \\
    pip install --no-index --no-cache-dir --find-links /tmp/wheelhouse \\
        -r /tmp/wheelhouse/requirements.txt \\
 && rm -rf /tmp/wheelhouse
LABEL swe-agent.image=deps
USER 10001:10001
WORKDIR /workspace
"""


@dataclass(frozen=True)
class BuiltImage:
    tag: str
    cached: bool
    requirements: list[str]


def _hash(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode())
        h.update(b"\x00")
    return h.hexdigest()[:16]


def _platform_args(settings: SandboxSettings) -> list[str]:
    major, minor = (int(x) for x in settings.manylinux_max.split("_"))
    plats = [f"manylinux_{major}_{m}_x86_64" for m in range(5, minor + 1)]
    plats += ["manylinux2014_x86_64", "manylinux2010_x86_64", "manylinux1_x86_64"]
    args: list[str] = []
    for p in plats:
        args += ["--platform", p]
    py = settings.python_version.replace(".", "")
    return [
        *args,
        "--python-version",
        settings.python_version,
        "--implementation",
        "cp",
        "--abi",
        f"cp{py}",
    ]


class ImageBuilder:
    def __init__(self, settings: SandboxSettings) -> None:
        self.s = settings

    def ensure_base(self) -> BuiltImage:
        tag = f"swe-agent/sandbox-base:{_hash(self.s.base_image, BASE_DOCKERFILE)}"
        if image_exists(self.s.docker_bin, tag):
            return BuiltImage(tag, True, [])
        with tempfile.TemporaryDirectory(prefix="swe-base-") as ctx:
            (Path(ctx) / "Dockerfile").write_text(BASE_DOCKERFILE)
            proc = docker(
                self.s.docker_bin,
                [
                    "build",
                    "--network",
                    "none",
                    "--build-arg",
                    f"BASE_IMAGE={self.s.base_image}",
                    "-t",
                    tag,
                    ctx,
                ],
                timeout=self.s.build_timeout_s,
                check=False,
            )
        if proc.returncode != 0:
            raise EnvSetupError(f"base image build failed: {proc.stderr.strip()[-1500:]}")
        return BuiltImage(tag, False, [])

    def ensure_deps(self, requirements: list[str]) -> BuiltImage:
        base = self.ensure_base()
        reqs = sorted(set(requirements) | set(self.s.extra_requirements))
        key = _hash(base.tag, self.s.python_version, self.s.manylinux_max, *reqs)
        tag = f"swe-agent/sandbox-deps:{key}"
        if image_exists(self.s.docker_bin, tag):
            return BuiltImage(tag, True, reqs)
        with tempfile.TemporaryDirectory(prefix="swe-deps-") as ctx_s:
            ctx = Path(ctx_s)
            wheelhouse = ctx / "wheelhouse"
            wheelhouse.mkdir()
            (wheelhouse / "requirements.txt").write_text("\n".join(reqs) + "\n")
            self._download_wheels(wheelhouse)
            (ctx / "Dockerfile").write_text(DEPS_DOCKERFILE)
            proc = docker(
                self.s.docker_bin,
                [
                    "build",
                    "--network",
                    "none",
                    "--build-arg",
                    f"BASE={base.tag}",
                    "-t",
                    tag,
                    str(ctx),
                ],
                timeout=self.s.build_timeout_s,
                check=False,
            )
        if proc.returncode != 0:
            raise EnvSetupError(f"dependency image build failed: {proc.stderr.strip()[-1500:]}")
        return BuiltImage(tag, False, reqs)

    def _download_wheels(self, wheelhouse: Path) -> None:
        argv = [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--quiet",
            "--disable-pip-version-check",
            "--only-binary=:all:",
            "--dest",
            str(wheelhouse),
            *_platform_args(self.s),
            "-r",
            str(wheelhouse / "requirements.txt"),
        ]
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=self.s.build_timeout_s, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise EnvSetupError("dependency download timed out") from exc
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout).strip()[-1500:]
            hint = (
                " (a dependency may only ship a source distribution; V1 installs wheels only)"
                if "No matching distribution" in tail or "ResolutionImpossible" in tail
                else ""
            )
            raise EnvSetupError(f"dependency download failed{hint}: {tail}")


def remove_images(settings: SandboxSettings, prefix: str = "swe-agent/sandbox-deps") -> int:
    proc = docker(
        settings.docker_bin, ["images", "--format", "{{.Repository}}:{{.Tag}}", prefix], check=False
    )
    tags = [t for t in proc.stdout.split() if t.startswith(prefix)]
    if tags:
        try:
            docker(settings.docker_bin, ["rmi", "-f", *tags], check=False)
        except SandboxError:
            return 0
    return len(tags)
