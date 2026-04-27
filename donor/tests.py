from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from donor.forms import DonorForm
from donor import models as dmodels

from blood import models as blood_models


class DonorFormGeoTests(TestCase):
	def setUp(self):
		self.base_data = {
			'aadhaar_number': '123456789012',
			'bloodgroup': 'A+',
			'address': '221B Baker Street',
			'mobile': '1234567890',
			'sex': 'U',
		}

	def test_coordinates_optional_when_both_blank(self):
		form = DonorForm(data=self.base_data)
		self.assertTrue(form.is_valid(), form.errors)

	def test_rejects_partial_coordinate_submission(self):
		partial = {**self.base_data, 'latitude': '12.9716'}
		form = DonorForm(data=partial)
		self.assertFalse(form.is_valid())
		self.assertIn('Please provide both latitude and longitude or leave both blank.', form.errors['__all__'])

	def test_accepts_valid_coordinate_pair(self):
		data = {
			**self.base_data,
			'latitude': '12.971598',
			'longitude': '77.594566',
		}
		form = DonorForm(data=data)
		self.assertTrue(form.is_valid(), form.errors)


@override_settings(GEOCODER_ALLOW_REMOTE=False)
class AdminDonorMapViewTests(TestCase):
	def setUp(self):
		self.client = Client()
		self.admin = User.objects.create_superuser('admin', 'admin@example.com', 'pass1234')

		self.donor_with_coords_verified = self._create_donor(
			username='verified_user',
			first_name='Verified',
			latitude=Decimal('12.971598'),
			longitude=Decimal('77.594566'),
			location_verified=True,
		)

		self.donor_with_coords_pending = self._create_donor(
			username='pending_user',
			first_name='Pending',
			latitude=Decimal('28.613939'),
			longitude=Decimal('77.209023'),
			location_verified=False,
		)

		self.donor_without_coords = self._create_donor(
			username='no_coords',
			first_name='NoCoords',
			latitude=None,
			longitude=None,
			address='Unknown Address 12345',
		)

	def _create_donor(self, username, first_name, latitude, longitude, location_verified=False, address='Test Address'):
		user = User.objects.create_user(username=username, password='password', first_name=first_name, last_name='Donor')
		return dmodels.Donor.objects.create(
			user=user,
			bloodgroup='O+',
			address=address,
			mobile='9999999999',
			latitude=latitude,
			longitude=longitude,
			location_verified=location_verified,
		)

	def test_admin_donor_map_context_counts(self):
		self.client.force_login(self.admin)
		response = self.client.get(reverse('admin-donor-map'))

		self.assertEqual(response.status_code, 200)
		context = response.context

		self.assertEqual(context['total_donors'], 3)
		self.assertEqual(context['pin_ready'], 2)
		self.assertEqual(context['verified_count'], 1)
		self.assertEqual(context['pending_count'], 1)
		self.assertEqual(context['without_coordinates'], 1)

		listed_donors = context['donors']
		self.assertEqual(len(listed_donors), 2)  # only donors with coordinates appear
		self.assertTrue(all(d.latitude is not None and d.longitude is not None for d in listed_donors))

		markers = context['map_data']
		self.assertEqual(len(markers), 2)
		marker_ids = {marker['id'] for marker in markers}
		self.assertSetEqual(marker_ids, {self.donor_with_coords_verified.id, self.donor_with_coords_pending.id})

	def test_bulk_geocode_action_assigns_coordinates(self):
		self.client.force_login(self.admin)
		donor = self._create_donor(
			username='auto_geo',
			first_name='Auto',
			latitude=None,
			longitude=None,
		)
		donor.address = 'Test Address'
		donor.save(update_fields=['address'])

		response = self.client.post(reverse('admin-donor-map'), {'intent': 'bulk_geocode', 'limit': 5})
		self.assertEqual(response.status_code, 302)
		donor.refresh_from_db()
		self.assertIsNotNone(donor.latitude)
		self.assertIsNotNone(donor.longitude)
		self.assertFalse(donor.location_verified)


class DonorAutoGeocodeSignalTests(TestCase):
	def test_signal_assigns_coordinates_when_missing(self):
		user = User.objects.create_user('geo_user', password='password', first_name='Geo', last_name='Signal')
		donor = dmodels.Donor.objects.create(
			user=user,
			bloodgroup='B+',
			address='Test Address',
			mobile='1234567890',
		)
		self.assertIsNotNone(donor.latitude)
		self.assertIsNotNone(donor.longitude)
		self.assertEqual(str(donor.latitude), '12.971599')
		self.assertEqual(str(donor.longitude), '77.594566')


@override_settings(AUTO_SEED_APPOINTMENT_SLOTS=False)
class DonorAppointmentSlotCapacityTests(TestCase):
	def setUp(self):
		from django.contrib.auth.models import Group
		self.client = Client()
		self.group, _ = Group.objects.get_or_create(name='DONOR')
		self.user = User.objects.create_user(username='donor_user', password='pass1234', first_name='Donor', last_name='One')
		self.group.user_set.add(self.user)
		self.donor = dmodels.Donor.objects.create(
			user=self.user,
			bloodgroup='A+',
			address='Addr',
			mobile='1234567890',
			sex='U',
		)

		other_user = User.objects.create_user(username='donor_other', password='pass1234', first_name='Other', last_name='Donor')
		self.group.user_set.add(other_user)
		self.other_donor = dmodels.Donor.objects.create(
			user=other_user,
			bloodgroup='A+',
			address='Addr',
			mobile='1234567891',
			sex='U',
		)

	def test_full_slot_hidden_and_rejected(self):
		now = timezone.now()
		slot = blood_models.DonationAppointmentSlot.objects.create(
			start_at=now + timedelta(hours=2),
			end_at=now + timedelta(hours=3),
			capacity=1,
			is_active=True,
		)
		# Fill the slot with another donor's pending appointment.
		blood_models.DonationAppointment.objects.create(
			donor=self.other_donor,
			slot=slot,
			requested_for=slot.start_at,
			status=blood_models.DonationAppointment.STATUS_PENDING,
		)

		self.client.force_login(self.user)
		response = self.client.get(reverse('donor-appointments'))
		self.assertEqual(response.status_code, 200)
		slots = list(response.context['slots'])
		self.assertTrue(all(s.id != slot.id for s in slots))

		post = self.client.post(reverse('donor-appointments'), {'slot_id': slot.id, 'notes': 'x'}, follow=True)
		self.assertEqual(post.status_code, 200)
		self.assertContains(post, 'Selected slot is full', status_code=200)

	def test_duplicate_booking_blocked(self):
		now = timezone.now()
		slot = blood_models.DonationAppointmentSlot.objects.create(
			start_at=now + timedelta(hours=2),
			end_at=now + timedelta(hours=3),
			capacity=5,
			is_active=True,
		)

		blood_models.DonationAppointment.objects.create(
			donor=self.donor,
			slot=slot,
			requested_for=slot.start_at,
			status=blood_models.DonationAppointment.STATUS_PENDING,
		)

		self.client.force_login(self.user)
		post = self.client.post(reverse('donor-appointments'), {'slot_id': slot.id, 'notes': 'another'}, follow=True)
		self.assertEqual(post.status_code, 200)
		self.assertContains(post, 'already have an appointment request', status_code=200)
