# Locust + the vacancy load test in one image. Build from the repository root:
#   docker build -f docker/Dockerfile -t vacancy-loadtest .
#   docker run --rm -p 8089:8089 -v "$PWD/credentials.json:/run/credentials.json:ro" \
#       vacancy-loadtest --credentials-file /run/credentials.json
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

# 1. dependencies only: cached until pyproject.toml / uv.lock change
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 2. the project itself
COPY README.md locustfile.py credentials.example.json ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8089 5557
ENTRYPOINT ["locust", "-f", "locustfile.py"]
