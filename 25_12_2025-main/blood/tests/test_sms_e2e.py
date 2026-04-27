"""End-to-end-ish SMS service tests (no real AWS calls).

These tests use the same phone numbers provided for manual validation and
exercise the SMS workflows with mocked providers.
"""

from unittest.mock import MagicMock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from django.core.files.uploadedfile import SimpleUploadedFile

from blood.models import BloodRequest
from blood.services import sms as sms_service
from donor.models import BloodDonate, Donor, MedicalReport
from patient.models import Patient


PATIENT_PHONE_RAW = "9385425650"
DONOR_PHONE_RAW = "9361046558"
PATIENT_PHONE_E164 = "+919385425650"
DONOR_PHONE_E164 = "+919361046558"


@override_settings(
    AWS_SNS_ENABLED=True,
    AWS_SNS_REGION="ap-south-1",
    AWS_SNS_DEFAULT_COUNTRY_CODE="+91",
    AWS_SNS_MIN_NOTIFICATION_GAP_SECONDS=0,
    AWS_SNS_MAX_RECIPIENTS=0,
    AWS_SNS_SMS_TYPE="Transactional",
    AWS_SNS_SENDER_ID=None,
)
class SMSE2EWorkflowTests(TestCase):
    def setUp(self):
        self.patient_user = User.objects.create_user(
            username="e2e_patient",
            password="DemoPass123!",
            first_name="E2E",
            last_name="Patient",
        )
        self.patient = Patient.objects.create(
            user=self.patient_user,
            age=28,
            bloodgroup="O-",
            disease="E2E",
            doctorname="Dr E2E",
            address="560001",
            mobile=PATIENT_PHONE_RAW,
        )

        self.donor_user = User.objects.create_user(
            username="e2e_donor",
            password="DemoPass123!",
            first_name="E2E",
            last_name="Donor",
        )
        self.donor = Donor.objects.create(
            user=self.donor_user,
            bloodgroup="O-",
            address="560001",
            mobile=DONOR_PHONE_RAW,
            zipcode="560001",
            is_available=True,
            last_notified_at=None,
        )
        MedicalReport.objects.create(
            donor=self.donor,
            document=SimpleUploadedFile('report.pdf', b'%PDF-1.4 fake', content_type='application/pdf'),
            document_name='report.pdf',
        )

        self.request_urgent = BloodRequest.objects.create(
            patient=self.patient,
            request_by_donor=None,
            patient_name=self.patient.get_name,
            patient_age=self.patient.age,
            reason="E2E urgent request",
            bloodgroup="O-",
            unit=100,
            status="Pending",
            is_urgent=True,
            request_zipcode="560001",
        )

    def test_urgent_broadcast_sends_to_matching_donor(self):
        mock_sns = MagicMock()

        result = sms_service.notify_matched_donors(
            self.request_urgent,
            contact_number=self.patient.mobile,
            sns_client=mock_sns,
        )

        self.assertTrue(result.enabled)
        self.assertEqual(result.delivered, 1)
        self.assertEqual(len(result.recipients), 1)
        self.assertEqual(result.recipients[0], DONOR_PHONE_E164)

        self.assertEqual(mock_sns.publish.call_count, 1)
        _, kwargs = mock_sns.publish.call_args
        self.assertEqual(kwargs.get("PhoneNumber"), DONOR_PHONE_E164)
        self.assertIn("Urgent", kwargs.get("Message", ""))

        self.donor.refresh_from_db()
        self.assertIsNotNone(self.donor.last_notified_at)

    def test_requester_confirmation_normalizes_to_e164(self):
        mock_sender = MagicMock(return_value={"status": "success"})
        resp = sms_service.send_requester_confirmation(
            self.request_urgent,
            self.patient.mobile,
            sms_sender=mock_sender,
        )
        self.assertEqual(resp.get("status"), "success")
        args, _ = mock_sender.call_args
        self.assertEqual(args[0], PATIENT_PHONE_E164)

    def test_approval_sms_sends_to_patient_and_donor_india_template(self):
        mock_sender = MagicMock(return_value={"status": "success"})

        result = sms_service.notify_request_approved(self.request_urgent, sms_sender=mock_sender)

        self.assertEqual(result.get("status"), "sent")
        self.assertEqual(mock_sender.call_count, 2)

        (to1, msg1), (to2, msg2) = [call.args for call in mock_sender.call_args_list]
        self.assertIn(PATIENT_PHONE_E164, (to1, to2))
        self.assertIn(DONOR_PHONE_E164, (to1, to2))

        # India-safe short templates start with "BloodBridge:".
        self.assertTrue(str(msg1).startswith("BloodBridge:"))
        self.assertTrue(str(msg2).startswith("BloodBridge:"))

    def test_rejection_sms_sends_to_patient_india_template(self):
        mock_sender = MagicMock(return_value={"status": "success"})

        resp = sms_service.notify_request_rejected(
            self.request_urgent,
            reason="E2E reject",
            sms_sender=mock_sender,
        )

        self.assertEqual(resp.get("status"), "success")
        args, _ = mock_sender.call_args
        self.assertEqual(args[0], PATIENT_PHONE_E164)
        self.assertTrue(str(args[1]).startswith("BloodBridge:"))

    def test_donation_sms_approved_and_rejected(self):
        donation_approved = BloodDonate.objects.create(
            donor=self.donor,
            disease="E2E",
            age=30,
            bloodgroup="O-",
            unit=100,
            status="Approved",
        )
        donation_rejected = BloodDonate.objects.create(
            donor=self.donor,
            disease="E2E",
            age=30,
            bloodgroup="O-",
            unit=100,
            status="Rejected",
        )

        mock_sender = MagicMock(return_value={"status": "success"})

        resp_ok = sms_service.notify_donation_approved(donation_approved, sms_sender=mock_sender)
        self.assertEqual(resp_ok.get("status"), "success")
        args, _ = mock_sender.call_args
        self.assertEqual(args[0], DONOR_PHONE_E164)
        self.assertTrue(str(args[1]).startswith("BloodBridge:"))

        mock_sender.reset_mock()
        resp_no = sms_service.notify_donation_rejected(donation_rejected, reason="E2E", sms_sender=mock_sender)
        self.assertEqual(resp_no.get("status"), "success")
        args, _ = mock_sender.call_args
        self.assertEqual(args[0], DONOR_PHONE_E164)
        self.assertTrue(str(args[1]).startswith("BloodBridge:"))

    def test_all_sms_flows_smoke(self):
        """Single smoke test that exercises all SMS flows end-to-end-ish.

        This is intentionally redundant with the focused tests above, but gives a
        one-shot "everything passes" test for quick verification.
        """

        # 1) Urgent donor broadcast (SNS client mock)
        mock_sns = MagicMock()
        alert = sms_service.notify_matched_donors(
            self.request_urgent,
            contact_number=self.patient.mobile,
            sns_client=mock_sns,
        )
        self.assertEqual(alert.delivered, 1)
        self.assertEqual(alert.recipients, [DONOR_PHONE_E164])

        # 2) Requester confirmation (SMS sender mock)
        mock_sender = MagicMock(return_value={"status": "success"})
        confirm = sms_service.send_requester_confirmation(
            self.request_urgent,
            self.patient.mobile,
            sms_sender=mock_sender,
        )
        self.assertEqual(confirm.get("status"), "success")

        # 3) Approval SMS (patient + donor)
        mock_sender.reset_mock()
        approved = sms_service.notify_request_approved(self.request_urgent, sms_sender=mock_sender)
        self.assertEqual(approved.get("status"), "sent")
        self.assertEqual(mock_sender.call_count, 2)

        tos = {call.args[0] for call in mock_sender.call_args_list}
        self.assertIn(PATIENT_PHONE_E164, tos)
        self.assertIn(DONOR_PHONE_E164, tos)

        # 4) Rejection SMS (patient)
        mock_sender.reset_mock()
        rejected = sms_service.notify_request_rejected(
            self.request_urgent,
            reason="E2E",
            sms_sender=mock_sender,
        )
        self.assertEqual(rejected.get("status"), "success")
        self.assertEqual(mock_sender.call_count, 1)
        self.assertEqual(mock_sender.call_args.args[0], PATIENT_PHONE_E164)

        # 5) Donation approval + rejection (donor)
        donation_ok = BloodDonate.objects.create(
            donor=self.donor,
            disease="E2E",
            age=30,
            bloodgroup="O-",
            unit=100,
            status="Approved",
        )
        donation_no = BloodDonate.objects.create(
            donor=self.donor,
            disease="E2E",
            age=30,
            bloodgroup="O-",
            unit=100,
            status="Rejected",
        )

        mock_sender.reset_mock()
        resp_ok = sms_service.notify_donation_approved(donation_ok, sms_sender=mock_sender)
        self.assertEqual(resp_ok.get("status"), "success")
        self.assertEqual(mock_sender.call_args.args[0], DONOR_PHONE_E164)

        mock_sender.reset_mock()
        resp_no = sms_service.notify_donation_rejected(donation_no, reason="E2E", sms_sender=mock_sender)
        self.assertEqual(resp_no.get("status"), "success")
        self.assertEqual(mock_sender.call_args.args[0], DONOR_PHONE_E164)
