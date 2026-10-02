# Streamlit UI. It talks to the backend over HTTP only (SWE_API_URL, SWE_API_KEY).
# Build from this directory:  docker build -t swe-agent-frontend frontend
# Base image and pip CA can be overridden as in backend/Dockerfile.
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH HOME=/tmp \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
RUN python3 -m venv /opt/venv && useradd --system --uid 10001 --no-create-home --home-dir /tmp ui
COPY requirements.txt /app/frontend/requirements.txt
RUN --mount=type=secret,id=pip_ca,required=false \
    if [ -s /run/secrets/pip_ca ]; then export PIP_CERT=/run/secrets/pip_ca; fi; \
    pip install --no-cache-dir -r /app/frontend/requirements.txt
COPY . /app/frontend
WORKDIR /app
USER 10001
EXPOSE 8501
HEALTHCHECK --interval=10s --timeout=3s --retries=12 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=2)"
CMD ["streamlit", "run", "/app/frontend/app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
