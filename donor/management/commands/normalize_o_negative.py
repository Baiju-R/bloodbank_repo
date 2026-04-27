from __future__ import annotations

from typing import Optional

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from donor.models import Donor


VALID_BLOOD_GROUPS = {
    "A+",
    "A-",
    "B+",
    "B-",
    "AB+",
    "AB-",
    "O+",
    "O-",
}


def _norm(value: Optional[str]) -> str:
    return (value or "").strip().lower()


class Command(BaseCommand):
    help = "Change all O- donors to a new group, except the specified 'ironman' donor."

    def add_arguments(self, parser):
        parser.add_argument(
            "--from-group",
            default="O-",
            help="Blood group to change from (default: O-)",
        )
        parser.add_argument(
            "--to-group",
            default="O+",
            help="Blood group to change to (default: O+)",
        )
        parser.add_argument(
            "--ironman-username",
            default="ironman",
            help="Username of the donor that should remain O- (default: ironman)",
        )
        parser.add_argument(
            "--ironman-name",
            default="ironman",
            help="Fallback match on first/last name containing this token (default: ironman)",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually write changes to the database (default is dry-run).",
        )

    def handle(self, *args, **options):
        from_group = (options.get("from_group") or "O-").strip().upper()
        to_group = (options.get("to_group") or "O+").strip().upper()
        ironman_username = _norm(options.get("ironman_username") or "ironman")
        ironman_name = _norm(options.get("ironman_name") or "ironman")
        apply = bool(options.get("apply"))

        if from_group not in VALID_BLOOD_GROUPS:
            raise CommandError(f"Invalid --from-group: {from_group}")
        if to_group not in VALID_BLOOD_GROUPS:
            raise CommandError(f"Invalid --to-group: {to_group}")
        if from_group == to_group:
            raise CommandError("--from-group and --to-group must be different")

        # Identify the "ironman" donor (prefer username match, then name token match).
        ironman_q = Q(user__username__iexact=ironman_username)
        if ironman_name:
            ironman_q |= Q(user__first_name__icontains=ironman_name) | Q(user__last_name__icontains=ironman_name)

        ironman_candidates = (
            Donor.objects.select_related("user")
            .filter(ironman_q)
            .order_by("id")
        )

        if not ironman_candidates.exists():
            raise CommandError(
                "Could not find the 'ironman' donor. "
                "Pass the correct username via --ironman-username (recommended)."
            )

        ironman = ironman_candidates.first()

        # Donors to change: all from_group except ironman.
        targets = (
            Donor.objects.select_related("user")
            .filter(bloodgroup=from_group)
            .exclude(id=ironman.id)
            .order_by("id")
        )

        total_from_group = Donor.objects.filter(bloodgroup=from_group).count()
        target_count = targets.count()

        self.stdout.write(
            f"Ironman donor: id={ironman.id}, username={ironman.user.username}, name={ironman.get_name}, bloodgroup={ironman.bloodgroup}"
        )
        self.stdout.write(f"Found {total_from_group} donor(s) currently in {from_group}.")
        self.stdout.write(f"Will update {target_count} donor(s) from {from_group} -> {to_group}.")

        if not apply:
            self.stdout.write(self.style.WARNING("DRY-RUN: no changes written. Re-run with --apply to commit."))
            return

        with transaction.atomic():
            updated = targets.update(bloodgroup=to_group)

            # Ensure ironman stays at from_group.
            if ironman.bloodgroup != from_group:
                ironman.bloodgroup = from_group
                ironman.save(update_fields=["bloodgroup"])

        self.stdout.write(self.style.SUCCESS(f"Updated {updated} donor(s)."))
