#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def _load_dotenv() -> None:
    """Load environment variables from a local .env file if present."""

    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return

    # Loads from the current working directory by default.
    load_dotenv(override=False)


def main():
    """Run administrative tasks."""
    _load_dotenv()
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bloodbankmanagement.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
