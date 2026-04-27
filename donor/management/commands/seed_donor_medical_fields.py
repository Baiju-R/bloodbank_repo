from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from donor.models import Donor


class Command(BaseCommand):
    help = (
        "Backfill/randomize donor medical fields (sex/DOB/weight/Hb/BP/risk flags/last_donated_at). "
        "Useful for demo databases so smart recommendation scores are not all identical."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--seed",
            type=int,
            default=123,
            help="Random seed for reproducible results.",
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
        apply_changes = bool(options["apply"])
        limit = int(options["limit"]) if options["limit"] else 0

        qs = Donor.objects.select_related("user").order_by("id")
        if limit > 0:
            qs = qs[:limit]

        total = qs.count() if limit <= 0 else len(list(qs))
        # If we had to slice, we already evaluated; re-fetch for update.
        if limit > 0:
            donors = list(Donor.objects.select_related("user").order_by("id")[:limit])
        else:
            donors = list(qs)

        updated = 0
        now = timezone.now().date()

        for donor in donors:
            fields_to_update: list[str] = []

            # Sex
            if not donor.sex or donor.sex == "U":
                donor.sex = rng.choices(["M", "F", "O"], weights=[48, 48, 4], k=1)[0]
                fields_to_update.append("sex")

            # DOB (age 18-60)
            if donor.date_of_birth is None:
                age_years = rng.randint(18, 60)
                extra_days = rng.randint(0, 364)
                donor.date_of_birth = now - timedelta(days=age_years * 365 + extra_days)
                fields_to_update.append("date_of_birth")

            # Weight (50-95kg)
            if donor.weight_kg is None:
                donor.weight_kg = rng.randint(50, 95)
                fields_to_update.append("weight_kg")

            # Hemoglobin (one decimal)
            if donor.hemoglobin_g_dl is None:
                if donor.sex == "M":
                    hb = rng.uniform(13.0, 17.5)
                elif donor.sex == "F":
                    hb = rng.uniform(12.3, 16.5)
                else:
                    hb = rng.uniform(12.5, 16.8)
                donor.hemoglobin_g_dl = Decimal(f"{hb:.1f}")
                fields_to_update.append("hemoglobin_g_dl")

            # Blood pressure
            if donor.blood_pressure_systolic is None:
                donor.blood_pressure_systolic = rng.randint(105, 155)
                fields_to_update.append("blood_pressure_systolic")
            if donor.blood_pressure_diastolic is None:
                donor.blood_pressure_diastolic = rng.randint(65, 95)
                fields_to_update.append("blood_pressure_diastolic")

            # Risk flags (mostly false)
            if donor.has_chronic_disease is False and donor.chronic_disease_details == "":
                if rng.random() < 0.06:
                    donor.has_chronic_disease = True
                    donor.chronic_disease_details = rng.choice(["Diabetes", "Hypertension", "Asthma"])
                    fields_to_update.extend(["has_chronic_disease", "chronic_disease_details"])
            if donor.on_medication is False and donor.medication_details == "":
                if rng.random() < 0.08:
                    donor.on_medication = True
                    donor.medication_details = rng.choice(["Metformin", "Amlodipine", "Levothyroxine"])
                    fields_to_update.extend(["on_medication", "medication_details"])
            if donor.smokes is False:
                if rng.random() < 0.10:
                    donor.smokes = True
                    fields_to_update.append("smokes")

            # Zipcode (only if missing)
            if not donor.zipcode:
                donor.zipcode = f"{rng.randint(100000, 999999)}"
                fields_to_update.append("zipcode")

            # last_donated_at distribution:
            # - 45% None (never donated / unknown)
            # - 35% eligible (>= 70 days ago)
            # - 20% in recovery (< 56 days ago)
            if donor.last_donated_at is None:
                roll = rng.random()
                if roll < 0.45:
                    donor.last_donated_at = None
                elif roll < 0.80:
                    donor.last_donated_at = now - timedelta(days=rng.randint(70, 240))
                    fields_to_update.append("last_donated_at")
                else:
                    donor.last_donated_at = now - timedelta(days=rng.randint(1, 55))
                    fields_to_update.append("last_donated_at")

            if fields_to_update:
                updated += 1
                if apply_changes:
                    donor.save(update_fields=sorted(set(fields_to_update)))

        mode = "APPLIED" if apply_changes else "DRY-RUN"
        self.stdout.write(self.style.SUCCESS(f"{mode}: processed {total} donors; would update {updated}."))
        if not apply_changes:
            self.stdout.write(
                "Run again with --apply to write changes, e.g. `py manage.py seed_donor_medical_fields --apply --seed 123`."
            )
