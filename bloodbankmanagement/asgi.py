"""
ASGI config for bloodbankmanagement project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/3.0/howto/deployment/asgi/
"""

import os

try:
	from dotenv import load_dotenv  # type: ignore
except Exception:
	load_dotenv = None

from django.core.asgi import get_asgi_application

if load_dotenv:
	load_dotenv(override=False)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bloodbankmanagement.settings')

application = get_asgi_application()
