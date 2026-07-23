# Backend image — FastAPI + uvicorn.
#
# Runs with a single worker by default: several pieces of in-memory state
# (_SME_ENGINE, _SME_REFRESHING, the rate limiter) are single-process by
# design (see CLAUDE.md). Scaling to multiple workers/replicas needs those
# migrated to a shared store (e.g. Redis) first — don't just bump --workers.
FROM python:3.13-slim

WORKDIR /app

# libpq-dev + build-essential: psycopg2-binary ships wheels for most
# platforms, but this keeps the image buildable if a wheel isn't available
# for the target architecture.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
