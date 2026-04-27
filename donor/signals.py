from __future__ import annotations

import logging

from django.conf import settings
from django.db.models.signals import pre_save
from django.dispatch import receiver

from blood.services.geocoding import geocode_address
from .models import Donor

LOGGER = logging.getLogger(__name__)


@receiver(pre_save, sender=Donor)
def populate_coordinates_from_address(sender, instance: Donor, **kwargs):
	"""Auto-populate latitude/longitude when an address exists but coords are blank."""

	address = (instance.address or "").strip()
	if not address:
		return

	lat_missing = instance.latitude is None
	lng_missing = instance.longitude is None

	# Only geocode when either coordinate is absent to avoid overriding manual pins
	if not (lat_missing or lng_missing):
		return

	allow_remote = getattr(settings, "GEOCODER_ALLOW_REMOTE", True)
	result = geocode_address(address, country_bias=getattr(settings, "GEOCODER_COUNTRY_BIAS", None), allow_remote=allow_remote)
	if not result:
		LOGGER.debug("Unable to geocode donor address '%s'", address)
		return

	instance.latitude = result.latitude
	instance.longitude = result.longitude
	instance.location_verified = False
	LOGGER.debug("Assigned coordinates (%s, %s) to donor %s", result.latitude, result.longitude, instance)
