from __future__ import annotations

from typing import Optional

from django.conf import settings
from django.core.management.base import BaseCommand

from global_land_mask import globe

from blood.services.geocoding import geocode_address, synthetic_coordinate, GeocodeResult
from donor.models import Donor


def is_land_coordinate(latitude: Optional[float], longitude: Optional[float]) -> bool:
    """Return True when the provided coordinate pair sits on land."""
    if latitude is None or longitude is None:
        return False
    try:
        return bool(globe.is_land(float(latitude), float(longitude)))
    except Exception:  # pragma: no cover - guard against unexpected data ranges
        return False


class Command(BaseCommand):
    help = "Relocate donors whose coordinates fall in the ocean to deterministic land positions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            help="Maximum number of donors to inspect (default: all)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview the changes without writing to the database.",
        )
        parser.add_argument(
            "--synthetic-only",
            action="store_true",
            help="Skip remote geocoding and always fall back to deterministic synthetic coordinates.",
        )
        parser.add_argument(
            "--country",
            type=str,
            help="Override ISO country code hint for remote geocoding.",
        )

    def handle(self, *args, **options):
        limit = options.get("limit")
        dry_run = options.get("dry_run", False)
        synthetic_only = options.get("synthetic_only", False)
        country_bias = options.get("country") or getattr(settings, "GEOCODER_COUNTRY_BIAS", None)
        allow_remote = getattr(settings, "GEOCODER_ALLOW_REMOTE", True) and not synthetic_only

        queryset = Donor.objects.all().order_by("id")
        if limit:
            queryset = queryset[:limit]

        donors = list(queryset)
        total = len(donors)
        if not total:
            self.stdout.write(self.style.SUCCESS("No donors found."))
            return

        fixed = 0
        skipped_land = 0
        failures = 0
        dry_run_candidates = 0

        for donor in donors:
            lat = float(donor.latitude) if donor.latitude is not None else None
            lon = float(donor.longitude) if donor.longitude is not None else None
            if is_land_coordinate(lat, lon):
                skipped_land += 1
                continue

            result = self._resolve_coordinate(donor, allow_remote, country_bias)
            if not result:
                failures += 1
                self.stderr.write(
                    f"Unable to resolve land coordinate for donor #{donor.id} ({donor.get_name})."
                )
                continue

            if dry_run:
                dry_run_candidates += 1
                self.stdout.write(
                    f"DRY-RUN donor #{donor.id} would move to ({result.latitude}, {result.longitude}) via {result.provider}."
                )
                continue

            donor.latitude = result.latitude
            donor.longitude = result.longitude
            donor.location_verified = False
            donor.save(update_fields=["latitude", "longitude", "location_verified"])
            fixed += 1
            self.stdout.write(
                f"Updated donor #{donor.id} -> ({result.latitude}, {result.longitude}) via {result.provider}."
            )

        summary = (
            f"Analyzed {total} donors: {skipped_land} already on land, "
            f"{fixed if not dry_run else dry_run_candidates} needing relocation"
        )
        if dry_run:
            summary += " (dry-run previewed only)"
        if failures:
            summary += f"; {failures} still unresolved"
        self.stdout.write(self.style.SUCCESS(summary))

    def _resolve_coordinate(
        self,
        donor: Donor,
        allow_remote: bool,
        country_bias: Optional[str],
    ) -> Optional[GeocodeResult]:
        """Return a land-safe GeocodeResult for the given donor."""

        address_hint = (donor.address or "").strip()
        if not address_hint:
            address_hint = donor.get_name or f"donor-{donor.pk}"

        result: Optional[GeocodeResult] = None
        if allow_remote:
            result = geocode_address(address_hint, country_bias=country_bias, allow_remote=True)
            if result and not is_land_coordinate(float(result.latitude), float(result.longitude)):
                result = None

        if not result:
            result = synthetic_coordinate(address_hint)

        if result and not is_land_coordinate(float(result.latitude), float(result.longitude)):
            return None
        return result
