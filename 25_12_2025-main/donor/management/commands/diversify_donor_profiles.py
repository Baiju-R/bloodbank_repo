"""
Management command to diversify donor profiles into realistic tiers.

Creates a wide spread of donor quality so AI and Rule scores cover the full
0-100 range instead of clustering at 80-85.

Tiers:
  Elite (10%)   — perfect health, experienced donors, prime age, ideal vitals
  Strong (20%)  — healthy, minor imperfections, good vitals
  Average (30%) — some risk factors, borderline vitals, limited history
  Weak (25%)    — multiple issues, poor vitals, unavailable, or in recovery
  Poor (15%)    — incomplete profiles, serious health issues, ineligible ranges
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from donor.models import Donor, BloodDonate


class Command(BaseCommand):
    help = "Diversify donor profiles into realistic tiers for varied AI/Rule scores."

    def add_arguments(self, parser):
        parser.add_argument("--seed", type=int, default=42, help="Random seed.")
        parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run).")

    def handle(self, *args, **options):
        rng = random.Random(options["seed"])
        apply = options["apply"]
        today = timezone.now().date()

        donors = list(Donor.objects.select_related("user").order_by("id"))
        n = len(donors)
        if n == 0:
            self.stdout.write("No donors found.")
            return

        # Shuffle donor indices deterministically, then assign tiers
        indices = list(range(n))
        rng.shuffle(indices)

        tier_cuts = {
            "elite": int(n * 0.10),
            "strong": int(n * 0.30),   # cumulative 10+20=30%
            "average": int(n * 0.60),  # cumulative 30+30=60%
            "weak": int(n * 0.85),     # cumulative 60+25=85%
            # rest = poor
        }

        updated = 0
        tier_counts = {"elite": 0, "strong": 0, "average": 0, "weak": 0, "poor": 0}

        for rank, idx in enumerate(indices):
            donor = donors[idx]
            if rank < tier_cuts["elite"]:
                tier = "elite"
            elif rank < tier_cuts["strong"]:
                tier = "strong"
            elif rank < tier_cuts["average"]:
                tier = "average"
            elif rank < tier_cuts["weak"]:
                tier = "weak"
            else:
                tier = "poor"

            tier_counts[tier] += 1
            changed = self._apply_tier(donor, tier, rng, today)

            if changed and apply:
                donor.save()
                updated += 1
            elif changed:
                updated += 1

            self.stdout.write(
                f"  {'[DRY]' if not apply else '[SAVE]'} "
                f"ID={donor.id:3d} {donor.user.first_name:15s} "
                f"tier={tier:7s} BG={donor.bloodgroup:3s} "
                f"avail={donor.is_available} Hb={donor.hemoglobin_g_dl} "
                f"wt={donor.weight_kg} BP={donor.blood_pressure_systolic}/{donor.blood_pressure_diastolic} "
                f"chronic={donor.has_chronic_disease} med={donor.on_medication} smoke={donor.smokes}"
            )

        # Also diversify donation counts (create fake approved donations for elite/strong)
        if apply:
            self._diversify_donation_history(donors, indices, tier_cuts, rng, today)

        self.stdout.write(self.style.SUCCESS(
            f"\n{'Applied' if apply else 'Dry-run'}: {updated} donors modified.\n"
            f"Tier distribution: {tier_counts}"
        ))

    def _apply_tier(self, donor, tier, rng, today):
        """Mutate donor fields according to the tier. Returns True if changed."""
        changed = False

        if tier == "elite":
            # Perfect donors — prime age, ideal vitals, available, no risk factors
            donor.is_available = True
            donor.date_of_birth = today - timedelta(days=rng.randint(25 * 365, 40 * 365))
            donor.sex = rng.choice(["M", "F"])
            donor.weight_kg = rng.randint(65, 85)
            donor.hemoglobin_g_dl = Decimal(str(round(rng.uniform(14.5, 16.5), 1)))
            donor.blood_pressure_systolic = rng.randint(110, 125)
            donor.blood_pressure_diastolic = rng.randint(70, 80)
            donor.has_chronic_disease = False
            donor.chronic_disease_details = ""
            donor.on_medication = False
            donor.medication_details = ""
            donor.smokes = False
            donor.last_donated_at = today - timedelta(days=rng.randint(90, 300))
            changed = True

        elif tier == "strong":
            # Good donors — healthy but some minor imperfections
            donor.is_available = rng.random() < 0.85  # 85% available
            donor.date_of_birth = today - timedelta(days=rng.randint(20 * 365, 50 * 365))
            donor.sex = rng.choice(["M", "F"])
            donor.weight_kg = rng.randint(55, 90)
            donor.hemoglobin_g_dl = Decimal(str(round(rng.uniform(13.0, 15.5), 1)))
            # Some have slightly elevated BP
            donor.blood_pressure_systolic = rng.randint(105, 140)
            donor.blood_pressure_diastolic = rng.randint(65, 88)
            donor.has_chronic_disease = False
            donor.chronic_disease_details = ""
            donor.on_medication = rng.random() < 0.15  # 15% on mild meds
            donor.medication_details = rng.choice(["", "Vitamins", "Allergy medication"]) if donor.on_medication else ""
            donor.smokes = False
            donor.last_donated_at = today - timedelta(days=rng.randint(60, 400))
            changed = True

        elif tier == "average":
            # Moderate donors — one or two risk factors, borderline vitals
            donor.is_available = rng.random() < 0.70  # 70% available
            donor.date_of_birth = today - timedelta(days=rng.randint(18 * 365, 58 * 365))
            donor.sex = rng.choice(["M", "F", "O"])

            # Some underweight, some overweight
            donor.weight_kg = rng.choice([
                rng.randint(48, 55),   # 33% borderline weight
                rng.randint(56, 75),   # 33% normal
                rng.randint(76, 95),   # 33% overweight
            ])

            # Hemoglobin: some borderline low
            donor.hemoglobin_g_dl = Decimal(str(round(rng.uniform(11.5, 14.5), 1)))

            # BP: wider range, some borderline
            donor.blood_pressure_systolic = rng.randint(95, 150)
            donor.blood_pressure_diastolic = rng.randint(60, 95)

            # Some risk factors
            donor.has_chronic_disease = rng.random() < 0.25
            donor.chronic_disease_details = rng.choice(["Mild asthma", "Seasonal allergies", "Eczema"]) if donor.has_chronic_disease else ""
            donor.on_medication = rng.random() < 0.30
            donor.medication_details = rng.choice(["Inhaler", "Antihistamines", "Supplements"]) if donor.on_medication else ""
            donor.smokes = rng.random() < 0.20

            # Sporadic donation history
            if rng.random() < 0.5:
                donor.last_donated_at = today - timedelta(days=rng.randint(30, 500))
            else:
                donor.last_donated_at = None
            changed = True

        elif tier == "weak":
            # Problematic donors — multiple issues, poor vitals, many unavailable
            donor.is_available = rng.random() < 0.40  # only 40% available
            donor.date_of_birth = today - timedelta(days=rng.choice([
                rng.randint(18 * 365, 19 * 365),   # very young
                rng.randint(55 * 365, 65 * 365),    # older
                rng.randint(30 * 365, 45 * 365),    # normal age but other issues
            ]))
            donor.sex = rng.choice(["M", "F"])

            # Underweight or borderline
            donor.weight_kg = rng.randint(45, 58)

            # Low hemoglobin
            donor.hemoglobin_g_dl = Decimal(str(round(rng.uniform(10.0, 12.8), 1)))

            # Bad BP
            bp_issue = rng.choice(["high", "low", "mixed"])
            if bp_issue == "high":
                donor.blood_pressure_systolic = rng.randint(145, 165)
                donor.blood_pressure_diastolic = rng.randint(90, 105)
            elif bp_issue == "low":
                donor.blood_pressure_systolic = rng.randint(80, 95)
                donor.blood_pressure_diastolic = rng.randint(45, 60)
            else:
                donor.blood_pressure_systolic = rng.randint(135, 155)
                donor.blood_pressure_diastolic = rng.randint(55, 70)

            # Multiple risk factors
            donor.has_chronic_disease = rng.random() < 0.60
            donor.chronic_disease_details = rng.choice([
                "Diabetes Type 2", "Hypertension", "Thyroid disorder",
                "Iron deficiency", "Arthritis"
            ]) if donor.has_chronic_disease else ""
            donor.on_medication = rng.random() < 0.55
            donor.medication_details = rng.choice([
                "Metformin", "Amlodipine", "Levothyroxine",
                "Iron supplements", "Anti-inflammatory"
            ]) if donor.on_medication else ""
            donor.smokes = rng.random() < 0.40

            # Recently donated (in recovery) or never
            if rng.random() < 0.4:
                donor.last_donated_at = today - timedelta(days=rng.randint(10, 50))
            else:
                donor.last_donated_at = None
            changed = True

        elif tier == "poor":
            # Worst candidates — incomplete profiles, out-of-range values, ineligible
            donor.is_available = rng.random() < 0.20  # mostly unavailable

            profile_style = rng.choice(["incomplete", "unhealthy", "elderly"])

            if profile_style == "incomplete":
                # Missing critical fields
                donor.date_of_birth = None
                donor.sex = "U"
                donor.weight_kg = None if rng.random() < 0.5 else rng.randint(40, 50)
                donor.hemoglobin_g_dl = None if rng.random() < 0.6 else Decimal(str(round(rng.uniform(9.0, 11.0), 1)))
                donor.blood_pressure_systolic = None
                donor.blood_pressure_diastolic = None
                donor.has_chronic_disease = rng.random() < 0.3
                donor.on_medication = rng.random() < 0.3
                donor.smokes = rng.random() < 0.3
                donor.last_donated_at = None
            elif profile_style == "unhealthy":
                # All fields present but terrible values
                donor.date_of_birth = today - timedelta(days=rng.randint(25 * 365, 45 * 365))
                donor.sex = rng.choice(["M", "F"])
                donor.weight_kg = rng.randint(38, 48)  # underweight
                donor.hemoglobin_g_dl = Decimal(str(round(rng.uniform(8.0, 10.5), 1)))  # anemic
                donor.blood_pressure_systolic = rng.randint(160, 185)  # hypertensive
                donor.blood_pressure_diastolic = rng.randint(100, 115)
                donor.has_chronic_disease = True
                donor.chronic_disease_details = rng.choice([
                    "Hepatitis B carrier", "Severe anemia", "Heart condition",
                    "Autoimmune disorder"
                ])
                donor.on_medication = True
                donor.medication_details = rng.choice([
                    "Blood thinners", "Immunosuppressants", "Cardiac medication"
                ])
                donor.smokes = rng.random() < 0.50
                donor.last_donated_at = today - timedelta(days=rng.randint(5, 30))  # in recovery
            else:  # elderly
                donor.date_of_birth = today - timedelta(days=rng.randint(63 * 365, 72 * 365))
                donor.sex = rng.choice(["M", "F"])
                donor.weight_kg = rng.randint(50, 65)
                donor.hemoglobin_g_dl = Decimal(str(round(rng.uniform(10.5, 12.5), 1)))
                donor.blood_pressure_systolic = rng.randint(140, 170)
                donor.blood_pressure_diastolic = rng.randint(85, 100)
                donor.has_chronic_disease = True
                donor.chronic_disease_details = rng.choice(["Arthritis", "Diabetes", "COPD"])
                donor.on_medication = True
                donor.medication_details = "Multiple prescriptions"
                donor.smokes = rng.random() < 0.30
                donor.last_donated_at = None

            changed = True

        return changed

    def _diversify_donation_history(self, donors, indices, tier_cuts, rng, today):
        """Add/remove BloodDonate records to create realistic donation count spread."""
        from blood.models import Stock

        for rank, idx in enumerate(indices):
            donor = donors[idx]
            existing = BloodDonate.objects.filter(donor=donor, status="Approved").count()

            if rank < tier_cuts["elite"]:
                target = rng.randint(8, 15)   # Elite: experienced donors
            elif rank < tier_cuts["strong"]:
                target = rng.randint(3, 7)    # Strong: moderate experience
            elif rank < tier_cuts["average"]:
                target = rng.randint(1, 3)    # Average: limited
            elif rank < tier_cuts["weak"]:
                target = rng.randint(0, 1)    # Weak: almost none
            else:
                target = 0                     # Poor: no history

            if existing < target:
                # Create additional approved donations
                for i in range(target - existing):
                    days_ago = rng.randint(100, 800)
                    BloodDonate.objects.create(
                        donor=donor,
                        bloodgroup=donor.bloodgroup,
                        unit=1,
                        disease="",
                        age=rng.randint(20, 55),
                        status="Approved",
                        date=today - timedelta(days=days_ago),
                    )
