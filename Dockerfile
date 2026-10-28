# Build one runtime image for both the API and the worker.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    STORAGE_DIR=/data

WORKDIR /app

# Copy only the project metadata and application source into the image.
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

# Install locked runtime dependencies, but leave test-only tools out of the image.
RUN uv sync --locked --no-dev --no-editable \
    && groupadd --system app \
    && useradd --system --gid app --create-home app \
    && mkdir -p /data \
    && chown app:app /data

ENV PATH="/opt/venv/bin:${PATH}"

USER app
EXPOSE 8000

# Compose overrides this command for the worker container.
CMD ["uvicorn", "taskplatform.api:app", "--host", "0.0.0.0", "--port", "8000"]
