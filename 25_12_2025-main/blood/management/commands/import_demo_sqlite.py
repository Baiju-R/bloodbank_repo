import os
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Import the repo's demo db.sqlite3 data into the default database. "
        "Intended for one-time migration from bundled SQLite to Cloud SQL Postgres."
    )

    def handle(self, *args, **options):
        if "legacy" not in settings.DATABASES:
            raise CommandError(
                "LEGACY_DATABASE_URL is not configured. "
                "Set LEGACY_DATABASE_URL=sqlite:////tmp/legacy.sqlite3 (or another path)."
            )

        # Safety: avoid duplicate imports if the target DB already has users.
        User = get_user_model()
        if User.objects.using("default").exists():
            self.stdout.write("Skipping import: default DB already has users.")
            return

        source_sqlite = Path(getattr(settings, "BASE_DIR", Path.cwd())) / "db.sqlite3"
        if not source_sqlite.exists():
            raise CommandError(f"Source demo SQLite DB not found at: {source_sqlite}")

        legacy_url = os.getenv("LEGACY_DATABASE_URL") or ""
        legacy_path = os.getenv("LEGACY_SQLITE_PATH") or "/tmp/legacy.sqlite3"

        # If legacy is a sqlite file path, copy the bundled demo DB into /tmp so we can migrate it.
        if legacy_url.startswith("sqlite:"):
            legacy_file = Path(legacy_path)
            legacy_file.parent.mkdir(parents=True, exist_ok=True)
            if not legacy_file.exists():
                shutil.copy2(source_sqlite, legacy_file)
                self.stdout.write(f"Copied demo DB to {legacy_file}")

        self.stdout.write("Migrating legacy (SQLite) schema to current migrations...")
        call_command("migrate", database="legacy", interactive=False, verbosity=1)

        self.stdout.write("Dumping legacy data...")
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".json", delete=False) as tmp:
            fixture_path = tmp.name

        try:
            with open(fixture_path, "w", encoding="utf-8") as handle:
                call_command(
                    "dumpdata",
                    database="legacy",
                    use_natural_foreign_keys=True,
                    use_natural_primary_keys=True,
                    exclude=["contenttypes", "auth.permission"],
                    stdout=handle,
                    verbosity=1,
                )

            self.stdout.write("Loading data into default database...")
            call_command("loaddata", fixture_path, database="default", verbosity=1)
        finally:
            try:
                os.remove(fixture_path)
            except OSError:
                pass

        self.stdout.write(self.style.SUCCESS("Import completed."))
