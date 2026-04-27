from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q

from blood.services.geocoding import geocode_address, synthetic_coordinate
from donor.models import Donor


class Command(BaseCommand):
	help = "Populate donor latitude/longitude pairs using their saved mailing addresses."

	def add_arguments(self, parser):
		parser.add_argument('--force', action='store_true', help='Re-geocode donors even if coordinates already exist.')
		parser.add_argument('--limit', type=int, help='Maximum number of donors to process this run.')
		parser.add_argument('--dry-run', action='store_true', help='Preview geocoding results without saving changes.')
		parser.add_argument('--country', type=str, help='Override ISO country code hint for the geocoder.')
		parser.add_argument('--fallback', action='store_true', help='Generate deterministic synthetic coordinates when lookup fails.')
		parser.add_argument('--synthetic-only', action='store_true', help='Skip remote lookups and use deterministic coordinates for every donor (ideal for demo data).')

	def handle(self, *args, **options):
		queryset = Donor.objects.all()
		if not options['force']:
			queryset = queryset.filter(Q(latitude__isnull=True) | Q(longitude__isnull=True))

		queryset = queryset.order_by('id')
		limit = options.get('limit')
		if limit:
			queryset = queryset[:limit]

		donors = list(queryset)
		total = len(donors)
		if not total:
			self.stdout.write(self.style.SUCCESS('No donors require geocoding.'))
			return

		country_bias = options.get('country') or settings.GEOCODER_COUNTRY_BIAS
		allow_remote = getattr(settings, 'GEOCODER_ALLOW_REMOTE', True)

		success_count = 0
		fallback_count = 0
		failures = []
		use_synthetic_only = options.get('synthetic_only', False)
		allow_fallback = options.get('fallback', False) or use_synthetic_only

		for donor in donors:
			result = None
			if not use_synthetic_only:
				result = geocode_address(donor.address, country_bias=country_bias, allow_remote=allow_remote)

			if (use_synthetic_only or (allow_fallback and not result)):
				result = synthetic_coordinate(donor.address or donor.get_name)
				if result:
					fallback_count += 1
			if not result:
				failures.append(donor)
				self.stderr.write(f"Unable to geocode donor #{donor.id} ({donor.get_name})")
				continue

			if options['dry_run']:
				self.stdout.write(f"DRY-RUN #{donor.id} -> {result.latitude}, {result.longitude} [{result.provider}]")
				success_count += 1
				continue

			donor.latitude = result.latitude
			donor.longitude = result.longitude
			donor.location_verified = False
			donor.save(update_fields=['latitude', 'longitude', 'location_verified'])
			success_count += 1
			self.stdout.write(f"Updated donor #{donor.id} coordinates via {result.provider}")

		self.stdout.write(self.style.SUCCESS(f"Geocoded {success_count} of {total} donors."))
		if fallback_count:
			self.stdout.write(f"{fallback_count} donors used synthetic coordinates.")
		if failures:
			self.stdout.write(self.style.WARNING(f"{len(failures)} addresses could not be resolved."))
