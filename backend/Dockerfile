# Backend image: the FastAPI service (target `api`) and the job worker (target `worker`).
# Build from the repository root:
#   docker build -f backend/Dockerfile --target api    -t swe-agent-api .
#   docker build -f backend/Dockerfile --target worker -t swe-agent-worker .
#
# Without Docker Hub access, point the base images at local ones:
#   --build-arg PYTHON_IMAGE=swe-agent/ubuntu-python:noble   (scripts/build_local_base_image.sh)
#   --build-arg DOCKER_CLI_IMAGE=<image with /usr/local/bin/docker>
# Behind a TLS-intercepting proxy, pass its CA for pip (it is not stored in the image):
#   --secret id=pip_ca,src=/path/to/ca.crt

ARG PYTHON_IMAGE=python:3.12-slim
ARG DOCKER_CLI_IMAGE=docker:28-cli

# ---------------------------------------------------------------- build: install into a venv
FROM ${PYTHON_IMAGE} AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
RUN python3 -m venv /opt/venv
COPY backend/pyproject.toml /src/backend/pyproject.toml
COPY backend/swe_agent /src/backend/swe_agent
RUN --mount=type=secret,id=pip_ca,required=false \
    if [ -s /run/secrets/pip_ca ]; then export PIP_CERT=/run/secrets/pip_ca; fi; \
    /opt/venv/bin/pip install --no-cache-dir "/src/backend[otlp]"

FROM ${DOCKER_CLI_IMAGE} AS dockercli

# ---------------------------------------------------------------- api
FROM ${PYTHON_IMAGE} AS api
# git: workspace operations; ripgrep: code search (falls back to Python without it)
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ripgrep ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --no-create-home --home-dir /tmp swe
COPY --from=build /opt/venv /opt/venv
COPY benchmarks /app/benchmarks
COPY configs /app/configs
ENV PATH=/opt/venv/bin:$PATH \
    HOME=/tmp \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SWE_BENCHMARKS_DIR=/app/benchmarks
WORKDIR /app
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=12 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
# Apply migrations, then serve. The CLI refuses a non-local bind without SWE_API_KEY.
CMD ["sh", "-c", "swe-agent db upgrade && exec swe-agent api --host 0.0.0.0 --port 8000"]

# ---------------------------------------------------------------- worker
# Adds the Docker CLI: the worker starts sandbox containers through the host's Docker daemon
# (the socket is mounted by docker-compose.yml; see README "Sandbox security").
FROM api AS worker
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=dockercli /usr/local/libexec/docker/cli-plugins/docker-buildx /usr/local/libexec/docker/cli-plugins/docker-buildx
HEALTHCHECK NONE
CMD ["swe-agent", "worker"]
