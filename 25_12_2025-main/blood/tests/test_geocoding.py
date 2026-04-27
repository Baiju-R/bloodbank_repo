from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings

from global_land_mask import globe

from blood.services import geocoding
from donor import models as dmodels


class SyntheticCoordinateTests(TestCase):
    def test_default_coordinates_always_landlocked(self):
        addresses = [
            "221B Baker Street",
            "742 Evergreen Terrace",
            "4 Privet Drive",
            "1600 Amphitheatre Parkway",
            "12 Grimmauld Place",
        ]

        for address in addresses:
            result = geocoding.synthetic_coordinate(address)
            self.assertIsNotNone(result)
            lat = float(result.latitude)
            lon = float(result.longitude)
            in_region = any(
                region[1][0] <= lat <= region[1][1] and region[2][0] <= lon <= region[2][1]
                for region in geocoding.SAFE_LAND_REGIONS
            )
            self.assertTrue(in_region, f"{address} produced ({lat}, {lon}) outside land regions")
            second = geocoding.synthetic_coordinate(address)
            self.assertEqual(result.latitude, second.latitude)
            self.assertEqual(result.longitude, second.longitude)

    def test_custom_bounds_override_land_regions(self):
        result = geocoding.synthetic_coordinate(
            "custom override",
            lat_bounds=(0.0, 1.0),
            lon_bounds=(10.0, 11.0),
        )
        self.assertGreaterEqual(float(result.latitude), 0.0)
        self.assertLessEqual(float(result.latitude), 1.0)
        self.assertGreaterEqual(float(result.longitude), 10.0)
        self.assertLessEqual(float(result.longitude), 11.0)


class FixOceanDonorsCommandTests(TestCase):
    def setUp(self):
        self.ocean_donor = self._create_donor(
            username="ocean",
            first_name="Ocean",
            last_name="Dweller",
            latitude=Decimal("0.000000"),
            longitude=Decimal("0.000000"),
        )
        self.land_donor = self._create_donor(
            username="land",
            first_name="Land",
            last_name="Dweller",
            latitude=Decimal("20.000000"),
            longitude=Decimal("77.000000"),
        )
        self.missing_donor = self._create_donor(
            username="missing",
            first_name="Missing",
            last_name="Coords",
            latitude=None,
            longitude=None,
        )

    def _create_donor(self, username, first_name, last_name, latitude, longitude):
        user = User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="pass1234",
            first_name=first_name,
            last_name=last_name,
        )
        return dmodels.Donor.objects.create(
            user=user,
            bloodgroup="A+",
            address=f"{first_name} Street",
            mobile="9999999999",
            latitude=latitude,
            longitude=longitude,
        )

    @override_settings(GEOCODER_ALLOW_REMOTE=False)
    def test_command_reassigns_ocean_and_missing_coordinates(self):
        call_command("fix_ocean_donors", synthetic_only=True)

        self.ocean_donor.refresh_from_db()
        self.assertNotEqual(self.ocean_donor.latitude, Decimal("0.000000"))
        self.assertTrue(
            globe.is_land(float(self.ocean_donor.latitude), float(self.ocean_donor.longitude))
        )

        self.missing_donor.refresh_from_db()
        self.assertIsNotNone(self.missing_donor.latitude)
        self.assertIsNotNone(self.missing_donor.longitude)
        self.assertTrue(
            globe.is_land(float(self.missing_donor.latitude), float(self.missing_donor.longitude))
        )

        self.land_donor.refresh_from_db()
        self.assertEqual(self.land_donor.latitude, Decimal("20.000000"))
        self.assertEqual(self.land_donor.longitude, Decimal("77.000000"))

    @override_settings(GEOCODER_ALLOW_REMOTE=False)
    def test_dry_run_leaves_database_unchanged(self):
        call_command("fix_ocean_donors", synthetic_only=True, dry_run=True)

        self.ocean_donor.refresh_from_db()
        self.assertEqual(self.ocean_donor.latitude, Decimal("0.000000"))
        self.assertEqual(self.ocean_donor.longitude, Decimal("0.000000"))

        self.missing_donor.refresh_from_db()
        self.assertIsNone(self.missing_donor.latitude)
        self.assertIsNone(self.missing_donor.longitude)

        self.land_donor.refresh_from_db()
        self.assertEqual(self.land_donor.latitude, Decimal("20.000000"))
        self.assertEqual(self.land_donor.longitude, Decimal("77.000000"))
