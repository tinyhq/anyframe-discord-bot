FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml ./
RUN uv pip install --system --no-cache .

COPY app ./app

ENV PYTHONUNBUFFERED=1 \
    STATE_DB_PATH=/data/state.db

CMD ["python", "-m", "app.main"]
