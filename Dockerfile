# syntax=docker/dockerfile:1
# ---- build stage: install Python dependencies into a virtualenv ----
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
WORKDIR /build
# Prefer the pinned lockfile when present (see README "Dependency lockfile").
COPY backend/requirements.txt backend/requirements.loc[k] ./
RUN if [ -f requirements.lock ]; then /opt/venv/bin/pip install --require-hashes -r requirements.lock; \
    else /opt/venv/bin/pip install -r requirements.txt; fi

# ---- runtime stage: no compilers, no tests, no secrets ----
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app \
    FRONTEND_DIR=/app/frontend
RUN groupadd --system --gid 10001 clearpocket \
 && useradd --system --uid 10001 --gid clearpocket --no-create-home --shell /usr/sbin/nologin clearpocket
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./alembic.ini
# Static frontend (served by the same process through an explicit allow-list)
COPY clearpocket-budget.html styles.css app.js ./frontend/
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"
# Production ASGI server (no --reload). Proxy headers are honoured only from FORWARDED_ALLOW_IPS.
# Access logging is off because request URLs contain search terms (private data); the app logs
# its own events without emails, tokens or bodies.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-access-log"]
