"""Thin wrapper around the docker CLI.

We call the CLI with argv lists instead of using the Docker SDK: no extra dependency, and the
exact command is visible in the trajectory. The CLI gets a minimal environment, so host secrets
in environment variables never reach the daemon or the containers (a container only gets the
variables passed explicitly with `-e`).
"""

from __future__ import annotations

import os
import subprocess

from swe_agent.core.errors import SweAgentError

_DOCKER_ENV_PASSTHROUGH = (
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_CONFIG",
    "DOCKER_CERT_PATH",
    "DOCKER_TLS_VERIFY",
    "XDG_RUNTIME_DIR",
)


class SandboxError(SweAgentError):
    """Docker is unavailable or failed for infrastructure reasons."""


class EnvSetupError(SweAgentError):
    """The sandbox image could not be built for this repository (dependencies, base image)."""


def docker_env() -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}
    env["HOME"] = os.environ.get("HOME", "/tmp")  # the CLI keeps its config under $HOME/.docker
    for key in _DOCKER_ENV_PASSTHROUGH:
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def docker(
    docker_bin: str,
    args: list[str],
    *,
    timeout: float = 60.0,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            [docker_bin, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=docker_env(),
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise SandboxError(f"docker CLI not found: {docker_bin}") from exc
    except subprocess.TimeoutExpired as exc:
        raise SandboxError(f"docker {args[0]} timed out after {timeout}s") from exc
    if check and proc.returncode != 0:
        raise SandboxError(
            f"docker {args[0]} failed ({proc.returncode}): {proc.stderr.strip()[:800]}"
        )
    return proc


def docker_available(docker_bin: str = "docker") -> tuple[bool, str]:
    try:
        proc = docker(
            docker_bin, ["version", "--format", "{{.Server.Version}}"], timeout=15, check=False
        )
    except SandboxError as exc:
        return False, str(exc)
    if proc.returncode != 0:
        return False, proc.stderr.strip()[:300] or "docker daemon not reachable"
    return True, proc.stdout.strip()


def image_exists(docker_bin: str, tag: str) -> bool:
    proc = docker(docker_bin, ["image", "inspect", "--format", "{{.Id}}", tag], check=False)
    return proc.returncode == 0
