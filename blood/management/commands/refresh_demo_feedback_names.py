import random

from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from blood.models import Feedback


class Command(BaseCommand):
    help = "Replace anonymous seeded demo feedback display names with realistic unique names."

    def add_arguments(self, parser):
        parser.add_argument("--seed", type=int, default=123, help="Random seed for deterministic output")
        parser.add_argument("--apply", action="store_true", help="Apply changes (default is dry-run)")

    def handle(self, *args, **options):
        Faker.seed(int(options["seed"]))
        random.seed(int(options["seed"]))
        faker = Faker()

        apply_changes = bool(options.get("apply"))

        qs = Feedback.objects.filter(
            is_seeded_demo=True,
            author_type=Feedback.AUTHOR_ANONYMOUS,
        ).order_by("id")

        used_names = set(
            name.lower()
            for name in Feedback.objects.filter(author_type=Feedback.AUTHOR_ANONYMOUS)
            .exclude(display_name="")
            .values_list("display_name", flat=True)
        )

        def unique_display_name() -> str:
            for _ in range(60):
                name = f"{faker.first_name()} {faker.last_name()}".strip()
                name = name[:60].strip()
                if name and name.lower() not in used_names:
                    used_names.add(name.lower())
                    return name
            suffix = random.randint(1000, 9999)
            name = f"{faker.first_name()} {faker.last_name()} {suffix}"[:60].strip()
            used_names.add(name.lower())
            return name

        planned = []
        for fb in qs:
            old = (fb.display_name or "").strip()
            if not old or old.lower().startswith("demo feedback"):
                planned.append((fb.id, old, unique_display_name()))

        if not planned:
            self.stdout.write(self.style.SUCCESS("No seeded anonymous demo feedback names to refresh."))
            return

        self.stdout.write(f"Will update {len(planned)} feedback display name(s).")
        for fb_id, old, new in planned[:10]:
            self.stdout.write(f"- #{fb_id}: '{old}' -> '{new}'")
        if len(planned) > 10:
            self.stdout.write(f"(showing 10 of {len(planned)})")

        if not apply_changes:
            self.stdout.write(self.style.WARNING("Dry-run only. Re-run with --apply to persist."))
            return

        with transaction.atomic():
            for fb_id, _old, new in planned:
                Feedback.objects.filter(id=fb_id).update(display_name=new)

        self.stdout.write(self.style.SUCCESS("Demo feedback names refreshed."))
