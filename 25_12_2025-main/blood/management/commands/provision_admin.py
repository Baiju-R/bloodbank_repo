import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Provision an admin (superuser) account from environment variables."

    def handle(self, *args, **options):
        username = (os.getenv("ADMIN_USERNAME") or "").strip()
        password = os.getenv("ADMIN_PASSWORD") or ""
        email = (os.getenv("ADMIN_EMAIL") or "").strip()
        reset_password = (os.getenv("ADMIN_RESET_PASSWORD") or "false").lower() == "true"

        if not username or not password:
            self.stdout.write(
                "Skipping admin provisioning (ADMIN_USERNAME/ADMIN_PASSWORD not set)."
            )
            return

        User = get_user_model()

        with transaction.atomic():
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "email": email,
                    "is_staff": True,
                    "is_superuser": True,
                },
            )

            changed = False

            if created:
                user.set_password(password)
                user.save()
                self.stdout.write(f"Created admin user: {username}")
                return

            if email and user.email != email:
                user.email = email
                changed = True

            if not getattr(user, "is_staff", False):
                user.is_staff = True
                changed = True

            if not getattr(user, "is_superuser", False):
                user.is_superuser = True
                changed = True

            if reset_password:
                user.set_password(password)
                changed = True

            if changed:
                user.save()
                self.stdout.write(f"Updated admin user: {username}")
            else:
                self.stdout.write(f"Admin user already present: {username}")
