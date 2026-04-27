#!/bin/sh
set -eu

# Cloud Run note: the container filesystem may not be reliably writable across layers.
# If DATABASE_URL isn't explicitly set, default SQLite to /tmp (writable) on Cloud Run.
if [ -n "${K_SERVICE:-}" ] && [ -z "${DATABASE_URL:-}" ]; then
  export DATABASE_URL="sqlite:////tmp/db.sqlite3"
  echo "DATABASE_URL not set; defaulting to ${DATABASE_URL}"
fi

# Demo-friendly default: ensure the DB schema exists.
# On Cloud Run + SQLite this creates tables inside the instance filesystem.
if [ "${MIGRATE_ON_STARTUP:-true}" = "true" ]; then
  echo "Running migrations..."
  python manage.py migrate --noinput
fi

# Optional: one-time import of the bundled demo SQLite data into the default DB.
# Safe by default: the command skips if the default DB already has users.
if [ "${IMPORT_DEMO_SQLITE_ON_STARTUP:-false}" = "true" ]; then
  python manage.py import_demo_sqlite
fi

# Optional: ensure an admin login exists (non-interactive).
# Controlled via env vars: PROVISION_ADMIN_ON_STARTUP, ADMIN_USERNAME, ADMIN_PASSWORD, ADMIN_EMAIL.
if [ "${PROVISION_ADMIN_ON_STARTUP:-false}" = "true" ]; then
  python manage.py provision_admin
fi

# Optional: warm-up staticfiles manifest checks (no-op if already collected)
# python manage.py collectstatic --noinput

exec gunicorn bloodbankmanagement.wsgi:application \
  --bind 0.0.0.0:${PORT:-8080} \
  --workers ${GUNICORN_WORKERS:-1} \
  --access-logfile ${GUNICORN_ACCESS_LOGFILE:--} \
  --error-logfile ${GUNICORN_ERROR_LOGFILE:--} \
  --timeout ${GUNICORN_TIMEOUT:-120}
