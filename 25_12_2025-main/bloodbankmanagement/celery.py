import os

from celery import Celery

try:
	from dotenv import load_dotenv  # type: ignore
except Exception:
	load_dotenv = None


if load_dotenv:
	load_dotenv(override=False)


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bloodbankmanagement.settings")

app = Celery("bloodbankmanagement")

# Load any CELERY_* settings from Django settings.py
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks from installed apps
app.autodiscover_tasks()
