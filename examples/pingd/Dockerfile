# The finished pingd image: multi-stage (ch7), slim base (ch8), BuildKit cache
# mounts (ch9), non-root with a read-only-friendly layout (ch24), healthcheck and
# correct signal handling (ch26).
#
#   docker build -t pingd:latest .
#   docker run --rm -p 8000:8000 pingd:latest
#
# syntax=docker/dockerfile:1

# ---------- build stage ----------
FROM python:3.12-slim AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies into a self-contained virtualenv we can copy wholesale.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
# Cache mount: pip's HTTP cache survives between builds without landing in a layer.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# ---------- runtime stage ----------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PINGD_VERSION=dev

# Unprivileged, fixed UID so bind-mount ownership is predictable (ch12).
RUN groupadd --gid 10001 pingd \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin pingd

COPY --from=build /opt/venv /opt/venv

WORKDIR /app
COPY --chown=10001:10001 app/ ./app/

USER 10001:10001

EXPOSE 8000

# Liveness only, cheap, and it must not depend on the database (ch26).
HEALTHCHECK --interval=10s --timeout=2s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=1.5).status==200 else 1)"

# Exec form: uvicorn becomes PID 1 and receives SIGTERM directly.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
