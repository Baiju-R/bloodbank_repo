from __future__ import annotations

import random
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from donor.models import Donor


class Command(BaseCommand):
    help = (
        "Balance donor availability so the demo dataset feels natural (some donors marked unavailable). "
        "Defaults to dry-run; pass --apply to write changes."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--seed",
            type=int,
            default=123,
            help="Random seed for reproducible selection.",
        )
        parser.add_argument(
            "--ratio-unavailable",
            type=float,
            default=0.18,
            help="Target fraction of donors to mark unavailable (0.0-0.9). Default: 0.18",
        )
        parser.add_argument(
            "--lookback-days",
            type=int,
            default=30,
            help="Set availability_updated_at within the last N days. Default: 30",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Optional limit on donors processed (0 = all).",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually write changes to DB (default is dry-run).",
        )

    def handle(self, *args, **options):
        rng = random.Random(int(options["seed"]))
        ratio_unavailable = float(options["ratio_unavailable"])
        lookback_days = int(options["lookback_days"])
        limit = int(options["limit"]) if options.get("limit") else 0
        apply_changes = bool(options.get("apply"))

        if ratio_unavailable < 0.0 or ratio_unavailable > 0.9:
            raise CommandError("--ratio-unavailable must be between 0.0 and 0.9")
        if lookback_days < 1 or lookback_days > 3650:
            raise CommandError("--lookback-days must be between 1 and 3650")

        qs = Donor.objects.select_related("user").order_by("bloodgroup", "id")
        if limit > 0:
            donors = list(qs[:limit])
        else:
            donors = list(qs)

        if not donors:
            self.stdout.write(self.style.WARNING("No donors found."))
            return

        # Stratified selection by bloodgroup (keeps distribution realistic across groups).
        by_group: dict[str, list[Donor]] = {}
        for donor in donors:
            by_group.setdefault((donor.bloodgroup or "").strip() or "UNKNOWN", []).append(donor)

        selected_unavailable_ids: set[int] = set()
        for group, group_donors in sorted(by_group.items(), key=lambda kv: kv[0]):
            group_size = len(group_donors)
            if group_size <= 0:
                continue
            target_k = int(round(group_size * ratio_unavailable))
            target_k = max(0, min(group_size, target_k))

            # Stable shuffle using RNG.
            shuffled = group_donors[:]
            rng.shuffle(shuffled)
            for d in shuffled[:target_k]:
                selected_unavailable_ids.add(d.id)

        now = timezone.now()
        update_unavailable: list[Donor] = []
        update_available: list[Donor] = []

        for donor in donors:
            should_be_available = donor.id not in selected_unavailable_ids
            if donor.is_available != should_be_available:
                donor.is_available = should_be_available
                # Make timestamp look natural.
                donor.availability_updated_at = now - timedelta(
                    days=rng.randint(0, lookback_days - 1),
                    hours=rng.randint(0, 23),
                    minutes=rng.randint(0, 59),
                )
                if should_be_available:
                    update_available.append(donor)
                else:
                    update_unavailable.append(donor)

        planned = len(update_available) + len(update_unavailable)
        total = len(donors)
        target_unavailable = len(selected_unavailable_ids)

        self.stdout.write(
            f"Target unavailable: {target_unavailable}/{total} ({(target_unavailable/total)*100:.1f}%) "
            f"using ratio {ratio_unavailable:.2f}"
        )
        self.stdout.write(
            f"Will change {planned} donors (set unavailable: {len(update_unavailable)}, set available: {len(update_available)})."
        )

        def _preview(items: list[Donor], label: str):
            if not items:
                return
            self.stdout.write(f"\nPreview {label} (showing up to 10):")
            for d in items[:10]:
                self.stdout.write(
                    f"- id={d.id} username={getattr(d.user, 'username', '')} bloodgroup={d.bloodgroup}"
                )

        _preview(update_unavailable, "set UNAVAILABLE")
        _preview(update_available, "set AVAILABLE")

        if not apply_changes:
            self.stdout.write(self.style.WARNING("DRY-RUN: no changes written. Re-run with --apply to commit."))
            return

        with transaction.atomic():
            if update_unavailable:
                Donor.objects.bulk_update(update_unavailable, ["is_available", "availability_updated_at"], batch_size=500)
            if update_available:
                Donor.objects.bulk_update(update_available, ["is_available", "availability_updated_at"], batch_size=500)

        self.stdout.write(self.style.SUCCESS("Availability balancing applied."))
