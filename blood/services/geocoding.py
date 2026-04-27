from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from typing import Dict, Optional, Sequence, Tuple

from django.conf import settings

# NOTE:
# `global_land_mask` is a heavy dependency (large data tables). Importing it at
# module import time can cause slow cold-starts or OOM in small containers.
# We import it lazily only when a land/ocean check is actually needed.
_land_globe = None


@lru_cache(maxsize=1)
def _get_land_globe():
	try:  # pragma: no cover - optional dependency
		from global_land_mask import globe
		return globe
	except ImportError:
		return None

try:  # pragma: no cover - import guard
	from geopy.geocoders import Nominatim
	from geopy.extra.rate_limiter import RateLimiter
except ImportError:  # pragma: no cover - handled gracefully at runtime
	Nominatim = None  # type: ignore
	RateLimiter = None  # type: ignore

LOGGER = logging.getLogger(__name__)

_DECIMAL_PLACES = Decimal("0.000001")

LandRegion = Tuple[str, Tuple[float, float], Tuple[float, float]]

# Hand-picked interior bounding boxes that never fall over an ocean tile.
# Each tuple stores (name, (lat_min, lat_max), (lon_min, lon_max)).
SAFE_LAND_REGIONS: Tuple[LandRegion, ...] = (
	("india-plateau", (11.5, 24.5), (74.0, 86.5)),
	("us-midwest", (35.0, 46.0), (-103.0, -86.0)),
	("brazil-cerrado", (-18.0, -5.0), (-60.0, -48.0)),
	("east-africa", (-3.0, 8.0), (30.0, 39.0)),
	("west-africa", (7.0, 14.0), (-3.0, 5.0)),
	("central-europe", (47.0, 54.0), (5.0, 20.0)),
	("se-asia", (13.0, 20.0), (100.0, 105.0)),
	("australia-east", (-33.0, -24.0), (135.0, 145.0)),
	("canada-prairies", (50.0, 56.0), (-110.0, -96.0)),
	("mexico-plateau", (18.5, 24.5), (-103.0, -98.0)),
)


@dataclass(frozen=True)
class GeocodeResult:
	"""Container describing an address lookup outcome."""

	latitude: Decimal
	longitude: Decimal
	provider: str = "static"
	accuracy: Optional[str] = None
	raw: Optional[Dict] = None


class GeocoderUnavailable(RuntimeError):
	"""Raised when a remote geocoder backend cannot be used."""


def _quantize(value: float | Decimal) -> Decimal:
	return Decimal(str(value)).quantize(_DECIMAL_PLACES, rounding=ROUND_HALF_UP)


def _fixture_table() -> Dict[str, Tuple[float, float]]:
	fixtures = getattr(settings, "GEOCODER_STATIC_FIXTURES", {}) or {}
	return {key.strip().lower(): value for key, value in fixtures.items() if isinstance(value, (tuple, list)) and len(value) == 2}


@lru_cache(maxsize=1)
def _get_fixtures() -> Dict[str, Tuple[float, float]]:
	return _fixture_table()


_GEOCODE_CACHE: Dict[str, GeocodeResult] = {}


def _get_rate_limited_geocode():
	if not Nominatim or not RateLimiter:
		raise GeocoderUnavailable("geopy is not installed; run pip install geopy")

	user_agent = getattr(settings, "GEOCODER_USER_AGENT", "bloodbridge-geocoder")
	timeout = getattr(settings, "GEOCODER_TIMEOUT", 10)
	min_delay = getattr(settings, "GEOCODER_MIN_DELAY_SECONDS", 1.0)

	geolocator = Nominatim(user_agent=user_agent, timeout=timeout)
	return RateLimiter(geolocator.geocode, min_delay_seconds=min_delay, swallow_exceptions=False)


@lru_cache(maxsize=1)
def _geocode_callable():
	return _get_rate_limited_geocode()


def _is_land_coordinate(latitude: Decimal, longitude: Decimal) -> bool:
	"""Best-effort guard ensuring generated pins land on solid ground."""

	land_globe = _land_globe or _get_land_globe()
	if land_globe is None:
		return True
	try:
		return bool(land_globe.is_land(float(latitude), float(longitude)))
	except Exception:  # pragma: no cover - depends on native lookup
		return False


def geocode_address(address: str, *, country_bias: Optional[str] = None, allow_remote: bool = True) -> Optional[GeocodeResult]:
	"""Resolve a postal address into coordinates.

	Parameters
	----------
	address:
		The textual address to geocode.
	country_bias:
		Optional ISO country code hint forwarded to the provider.
	allow_remote:
		When False, only static fixtures will be used (ideal for tests).
	"""

	if not address:
		return None

	normalized = address.strip()
	key = normalized.lower()

	# Static fixtures act as deterministic lookups for tests/demo content
	fixture = _get_fixtures().get(key)
	if fixture:
		result = GeocodeResult(
			latitude=_quantize(fixture[0]),
			longitude=_quantize(fixture[1]),
			provider="fixture",
			accuracy="exact",
		)
		_GEOCODE_CACHE[key] = result
		return result

	if key in _GEOCODE_CACHE:
		return _GEOCODE_CACHE[key]

	if not allow_remote:
		return None

	try:
		geocode_fn = _geocode_callable()
		location = geocode_fn(query=normalized, addressdetails=True, country_codes=country_bias)
	except GeocoderUnavailable:
		LOGGER.warning("Geocoder unavailable - install geopy to enable remote lookups")
		return None
	except Exception as exc:  # pragma: no cover - depends on network
		LOGGER.warning("Remote geocoding failed for '%s': %s", normalized, exc)
		return None

	if not location:
		LOGGER.info("No geocoding result for '%s'", normalized)
		return None

	result = GeocodeResult(
		latitude=_quantize(location.latitude),
		longitude=_quantize(location.longitude),
		provider="nominatim",
		accuracy=str(location.raw.get('type')) if isinstance(location.raw, dict) else None,
		raw=location.raw if isinstance(location.raw, dict) else None,
	)
	_GEOCODE_CACHE[key] = result
	return result


def synthetic_coordinate(
	address: str,
	*,
	lat_bounds: Optional[Tuple[float, float]] = None,
	lon_bounds: Optional[Tuple[float, float]] = None,
	land_regions: Optional[Sequence[LandRegion]] = None,
) -> Optional[GeocodeResult]:
	"""Generate deterministic coordinates within India bounds for demo data.

	This is useful when the stored textual addresses are synthetic (faker-generated)
	and cannot be resolved by a real geocoder. The output is stable for a given
	address string so map pins do not drift between runs.
	"""

	if not address:
		return None

	normalized = address.strip().lower()
	if not normalized:
		return None

	if (lat_bounds is None) ^ (lon_bounds is None):
		raise ValueError("lat_bounds and lon_bounds must be provided together")

	regions = tuple(land_regions or SAFE_LAND_REGIONS)
	if not regions and (lat_bounds is None or lon_bounds is None):
		return None

	digest = hashlib.sha256(normalized.encode("utf-8")).digest()
	lat_fraction = int.from_bytes(digest[1:9], "big") / 2 ** 64
	lon_fraction = int.from_bytes(digest[9:17], "big") / 2 ** 64

	if lat_bounds is not None and lon_bounds is not None:
		candidate_regions: Sequence[LandRegion] = (("custom-bounds", lat_bounds, lon_bounds),)
	else:
		start_index = digest[0] % len(regions)
		candidate_regions = tuple(
			regions[(start_index + offset) % len(regions)]
			for offset in range(len(regions))
		)

	for region_name, region_lat_bounds, region_lon_bounds in candidate_regions:
		lat = _quantize(
			region_lat_bounds[0]
			+ (region_lat_bounds[1] - region_lat_bounds[0]) * lat_fraction
		)
		lon = _quantize(
			region_lon_bounds[0]
			+ (region_lon_bounds[1] - region_lon_bounds[0]) * lon_fraction
		)
		if not _is_land_coordinate(lat, lon):
			continue

		return GeocodeResult(
			latitude=lat,
			longitude=lon,
			provider="synthetic",
			accuracy="approx",
			raw={
				"source": "deterministic-hash",
				"lat_bounds": region_lat_bounds,
				"lon_bounds": region_lon_bounds,
				"land_region": region_name,
			},
		)

	return None


__all__ = ["GeocodeResult", "GeocoderUnavailable", "geocode_address", "synthetic_coordinate", "SAFE_LAND_REGIONS"]
