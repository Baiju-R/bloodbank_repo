# Cloud Run / container-friendly Django image
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

# System deps (kept minimal). Add build tools only if you hit wheels/build issues.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/

RUN sed -i 's/\r$//' /app/entrypoint.sh \
    && chmod +x /app/entrypoint.sh

# Collect static at build time (WhiteNoise). No DB required.
RUN python manage.py collectstatic --noinput

EXPOSE 8080

CMD ["/app/entrypoint.sh"]
