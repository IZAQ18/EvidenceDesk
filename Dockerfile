FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    OMP_THREAD_LIMIT=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

RUN addgroup --system app && adduser --system --ingroup app app
WORKDIR /app

# Bump when refreshing Debian security fixes; unchanged cached RUN layers would
# otherwise reuse packages installed before fixes became available.
ARG OS_PACKAGE_REFRESH=2026-09-13
RUN test -n "$OS_PACKAGE_REFRESH" \
    && apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY app ./app
RUN mkdir -p /app/data && chown -R app:app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

# EvidenceDesk emits its own request log with path-only URLs and request IDs. Disabling
# Uvicorn's access log prevents OAuth codes and reset tokens in query strings reaching logs.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=* --no-access-log"]
