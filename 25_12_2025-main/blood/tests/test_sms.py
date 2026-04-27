"""Unit tests for the AWS SNS alert helper."""

from unittest.mock import MagicMock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from blood.models import BloodRequest
from blood.services import sms
from donor.models import Donor, MedicalReport
from patient.models import Patient


class SNSAlertTests(TestCase):
	def setUp(self):
		self.user_counter = 0

	def _create_donor(self, bloodgroup="A+", mobile="+15551230000"):
		self.user_counter += 1
		user = User.objects.create_user(
			username=f"donor{self.user_counter}",
			password="DemoPass123!",
			first_name="Test",
			last_name=f"Donor{self.user_counter}",
		)
		donor = Donor.objects.create(
			user=user,
			bloodgroup=bloodgroup,
			address="Test Address",
			mobile=mobile,
			is_available=True,
		)
		MedicalReport.objects.create(
			donor=donor,
			document=SimpleUploadedFile('report.pdf', b'%PDF-1.4 fake', content_type='application/pdf'),
			document_name='report.pdf',
		)
		return donor

	def _create_request(self, bloodgroup="A+", is_urgent=True):
		return BloodRequest.objects.create(
			patient=None,
			request_by_donor=None,
			patient_name="Unit Test",
			patient_age=35,
			reason="Need blood for surgery",
			bloodgroup=bloodgroup,
			unit=250,
			status="Pending",
			is_urgent=is_urgent,
			request_zipcode="560001",
		)

	def _create_patient(self, mobile="+15551239999"):
		self.user_counter += 1
		user = User.objects.create_user(
			username=f"patient{self.user_counter}",
			password="DemoPass123!",
			first_name="Test",
			last_name=f"Patient{self.user_counter}",
		)
		return Patient.objects.create(
			user=user,
			age=30,
			bloodgroup="A+",
			disease="Test",
			doctorname="Dr. Test",
			address="Test Address",
			mobile=mobile,
		)

	@override_settings(AWS_SNS_ENABLED=False)
	def test_notify_skips_when_disabled(self):
		blood_request = self._create_request()
		result = sms.notify_matched_donors(blood_request)
		self.assertEqual(result.delivered, 0)
		self.assertEqual(result.reason, "sns-disabled")

	@override_settings(
		AWS_SNS_ENABLED=True,
		AWS_SNS_MAX_RECIPIENTS=5,
		AWS_SNS_MIN_NOTIFICATION_GAP_SECONDS=0,
		AWS_SNS_DEFAULT_COUNTRY_CODE="+1",
	)
	def test_notify_publishes_to_eligible_donors(self):
		donor_one = self._create_donor(mobile="+1 (555) 123-4567")
		donor_two = self._create_donor(mobile="5551237777")
		blood_request = self._create_request()

		mock_client = MagicMock()

		result = sms.notify_matched_donors(blood_request, sns_client=mock_client)

		self.assertEqual(result.delivered, 2)
		self.assertEqual(len(result.recipients), 2)
		self.assertEqual(mock_client.publish.call_count, 2)

		donor_one.refresh_from_db()
		donor_two.refresh_from_db()
		self.assertIsNotNone(donor_one.last_notified_at)
		self.assertIsNotNone(donor_two.last_notified_at)

	@override_settings(AWS_SNS_ENABLED=True)
	def test_notify_request_approved_sends_patient_and_donor(self):
		donor = self._create_donor(mobile="+1 (555) 123-4567")
		patient = self._create_patient(mobile="+1 (555) 999-0000")
		blood_request = self._create_request()
		blood_request.patient = patient
		blood_request.save(update_fields=["patient"])

		mock_sender = MagicMock(return_value={"status": "success"})

		result = sms.notify_request_approved(blood_request, sms_sender=mock_sender)

		self.assertEqual(mock_sender.call_count, 2)
		self.assertEqual(result["status"], "sent")
		self.assertEqual(result["patient"]["status"], "success")
		self.assertEqual(result["donor"]["status"], "success")

	@override_settings(AWS_SNS_ENABLED=True)
	def test_notify_request_rejected_sends_patient(self):
		patient = self._create_patient(mobile="+1 (555) 111-2222")
		blood_request = self._create_request()
		blood_request.patient = patient
		blood_request.save(update_fields=["patient"])

		mock_sender = MagicMock(return_value={"status": "success"})

		result = sms.notify_request_rejected(
			blood_request,
			reason="Insufficient stock for this blood group.",
			sms_sender=mock_sender,
		)

		self.assertEqual(mock_sender.call_count, 1)
		self.assertEqual(result["status"], "success")
