"""Realistic user-journey tests that simulate real donor & patient workflows.

Each test class represents a real person's lifecycle through the blood bank:
- Patient signs up, makes requests, receives SMS + in-app notifications
- Donor signs up, gets matched to urgent requests, donates blood, receives SMS + in-app
- Admin approves / rejects at each step, triggering the correct notifications

All SMS calls are mocked (no real AWS charges). In-app notifications are verified
against the real DB.  Phone numbers mirror the two real devices:
  Patient phone: 9385425650  (+919385425650)
  Donor phone:   9361046558  (+919361046558)
"""

from unittest.mock import MagicMock, patch, call
from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, RequestFactory, override_settings
from django.utils import timezone

from blood.models import (
    BloodRequest, InAppNotification, Stock,
)
from blood.services import sms as sms_service
from blood.utils.phone import normalize_phone_number
from blood.views import (
    _create_inapp_notification_safe,
    _notify_request_owner_inapp,
)
from donor.models import BloodDonate, Donor, MedicalReport
from patient.models import Patient


def _add_valid_medical_report(donor):
    """Create a valid (non-expired) medical report for a donor."""
    MedicalReport.objects.create(
        donor=donor,
        document=SimpleUploadedFile('report.pdf', b'%PDF-1.4 fake', content_type='application/pdf'),
        document_name='report.pdf',
    )


# ── Constants matching the two real test phones ──────────────────────────────
PATIENT_PHONE = "9385425650"
PATIENT_E164 = "+919385425650"
DONOR_PHONE = "9361046558"
DONOR_E164 = "+919361046558"

SMS_SETTINGS = dict(
    AWS_SNS_ENABLED=True,
    AWS_SNS_REGION="ap-south-1",
    AWS_SNS_DEFAULT_COUNTRY_CODE="+91",
    AWS_SNS_MIN_NOTIFICATION_GAP_SECONDS=0,
    AWS_SNS_MAX_RECIPIENTS=0,
    AWS_SNS_SMS_TYPE="Transactional",
    AWS_SNS_SENDER_ID=None,
    CELERY_TASK_ALWAYS_EAGER=True,
)


def _seed_stock():
    """Ensure all 8 blood groups exist with baseline stock."""
    for bg in ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]:
        Stock.objects.get_or_create(bloodgroup=bg, defaults={"unit": 500})


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 1 — Patient journey: signup → request blood → receive notifications
# ═══════════════════════════════════════════════════════════════════════════════
@override_settings(**SMS_SETTINGS)
class PatientJourneyTest(TestCase):
    """Simulate a real patient's complete lifecycle."""

    def setUp(self):
        _seed_stock()

        # 1) Patient signs up (mirrors patientsignup_view)
        self.patient_user = User.objects.create_user(
            username="josephinmary",
            password="JosephinMary@123",
            first_name="Josephin",
            last_name="Mary",
        )
        Group.objects.get_or_create(name="PATIENT")
        self.patient_user.groups.add(Group.objects.get(name="PATIENT"))

        self.patient = Patient.objects.create(
            user=self.patient_user,
            age=28,
            bloodgroup="O-",
            disease="Anaemia",
            doctorname="Dr Ramesh",
            address="Chennai, TN 600001",
            mobile=PATIENT_PHONE,
        )

        # Also create a matching donor so approval has someone to recommend
        self.donor_user = User.objects.create_user(
            username="donor_mani",
            password="Mani@1234",
            first_name="Mani",
            last_name="Chow",
        )
        Group.objects.get_or_create(name="DONOR")
        self.donor_user.groups.add(Group.objects.get(name="DONOR"))

        self.donor = Donor.objects.create(
            user=self.donor_user,
            bloodgroup="O-",
            address="Chennai, TN 600002",
            mobile=DONOR_PHONE,
            zipcode="600001",
            is_available=True,
        )
        _add_valid_medical_report(self.donor)

    # ── Step 1: Patient submits an urgent blood request ──────────────────
    def test_step1_patient_submits_urgent_request_gets_confirmation_sms(self):
        """Patient submits urgent request → requester confirmation SMS + in-app."""

        request = BloodRequest.objects.create(
            patient=self.patient,
            patient_name=self.patient.get_name,
            patient_age=self.patient.age,
            reason="Need O- blood urgently for surgery",
            bloodgroup="O-",
            unit=200,
            status="Pending",
            is_urgent=True,
            request_zipcode="600001",
        )

        # In-app notification created on submission
        _create_inapp_notification_safe(
            patient=self.patient,
            title="Blood Request Submitted",
            message=f"Your blood request #{request.id} for 200ml (O-) has been submitted.",
            related_request=request,
        )

        notif = InAppNotification.objects.filter(patient=self.patient).first()
        self.assertIsNotNone(notif)
        self.assertIn("submitted", notif.message.lower())
        self.assertEqual(notif.related_request_id, request.id)

        # SMS: requester confirmation
        mock_sender = MagicMock(return_value={"status": "success", "message_id": "test-001"})
        resp = sms_service.send_requester_confirmation(
            request, self.patient.mobile, sms_sender=mock_sender
        )
        self.assertEqual(resp["status"], "success")
        to, msg = mock_sender.call_args.args
        self.assertEqual(to, PATIENT_E164)
        self.assertIn("BloodBridge", msg)
        self.assertIn(str(request.id), msg)

    # ── Step 2: Urgent request triggers donor broadcast ──────────────────
    def test_step2_urgent_request_broadcasts_to_matching_donor(self):
        """Urgent request → donor broadcast SMS sent to matching donor phone."""

        request = BloodRequest.objects.create(
            patient=self.patient,
            patient_name=self.patient.get_name,
            patient_age=self.patient.age,
            reason="Urgent O- surgery",
            bloodgroup="O-",
            unit=200,
            status="Pending",
            is_urgent=True,
            request_zipcode="600001",
        )

        mock_sns = MagicMock()
        result = sms_service.notify_matched_donors(
            request,
            contact_number=self.patient.mobile,
            sns_client=mock_sns,
        )

        self.assertTrue(result.enabled)
        self.assertEqual(result.delivered, 1)
        self.assertIn(DONOR_E164, result.recipients)

        # Verify donor received the alert
        _, kwargs = mock_sns.publish.call_args
        self.assertEqual(kwargs["PhoneNumber"], DONOR_E164)
        self.assertIn("Urgent", kwargs["Message"])
        self.assertIn("O-", kwargs["Message"])

        # Donor cooldown saved
        self.donor.refresh_from_db()
        self.assertIsNotNone(self.donor.last_notified_at)

    # ── Step 3: Admin approves → patient & donor get SMS + in-app ────────
    def test_step3_admin_approves_request_both_get_sms_and_inapp(self):
        """Admin approves blood request → patient + donor receive SMS + in-app notifications."""

        request = BloodRequest.objects.create(
            patient=self.patient,
            patient_name=self.patient.get_name,
            patient_age=self.patient.age,
            reason="O- for surgery",
            bloodgroup="O-",
            unit=100,
            status="Pending",
            is_urgent=True,
            request_zipcode="600001",
        )

        # Simulate what update_approve_status_view does
        stock = Stock.objects.get(bloodgroup="O-")
        old_stock = stock.unit
        stock.unit -= request.unit
        stock.save()
        request.status = "Approved"
        request.save()

        # In-app for patient (mimics _notify_request_owner_inapp)
        _notify_request_owner_inapp(
            request,
            title="Blood Request Approved",
            message=f"Your request #{request.id} for O- (100ml) has been approved.",
        )
        patient_notif = InAppNotification.objects.filter(
            patient=self.patient, title="Blood Request Approved"
        ).first()
        self.assertIsNotNone(patient_notif)
        self.assertIn("approved", patient_notif.message.lower())

        # In-app for donor (mimics the donor match notification in approve view)
        _create_inapp_notification_safe(
            donor=self.donor,
            title="New Approved Request Match",
            message=f"You are a top match for approved request #{request.id} (O-, 100ml).",
            related_request=request,
        )
        donor_notif = InAppNotification.objects.filter(
            donor=self.donor, title="New Approved Request Match"
        ).first()
        self.assertIsNotNone(donor_notif)
        self.assertIn("top match", donor_notif.message.lower())

        # SMS: approval notification to patient + donor
        mock_sender = MagicMock(return_value={"status": "success"})
        result = sms_service.notify_request_approved(request, sms_sender=mock_sender)
        self.assertEqual(result["status"], "sent")
        self.assertEqual(mock_sender.call_count, 2)

        sms_phones = {c.args[0] for c in mock_sender.call_args_list}
        self.assertIn(PATIENT_E164, sms_phones)
        self.assertIn(DONOR_E164, sms_phones)

        # India-safe short templates
        for c in mock_sender.call_args_list:
            self.assertTrue(c.args[1].startswith("BloodBridge:"))

        # Stock correctly deducted
        stock.refresh_from_db()
        self.assertEqual(stock.unit, old_stock - 100)

    # ── Step 4: Admin rejects → patient gets SMS + in-app ────────────────
    def test_step4_admin_rejects_request_patient_gets_sms_and_inapp(self):
        """Admin rejects blood request → patient receives rejection SMS + in-app."""

        request = BloodRequest.objects.create(
            patient=self.patient,
            patient_name=self.patient.get_name,
            patient_age=self.patient.age,
            reason="Need AB+ blood",
            bloodgroup="AB+",
            unit=50,
            status="Pending",
            is_urgent=False,
            request_zipcode="600001",
        )

        # Simulate rejection
        request.status = "Rejected"
        request.save()

        # In-app for patient
        _notify_request_owner_inapp(
            request,
            title="Blood Request Rejected",
            message=f"Your request #{request.id} for AB+ was rejected after admin review.",
        )
        notif = InAppNotification.objects.filter(
            patient=self.patient, title="Blood Request Rejected"
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn("rejected", notif.message.lower())

        # SMS: rejection
        mock_sender = MagicMock(return_value={"status": "success"})
        result = sms_service.notify_request_rejected(
            request, reason="Insufficient stock", sms_sender=mock_sender
        )
        self.assertEqual(result["status"], "success")
        to, msg = mock_sender.call_args.args
        self.assertEqual(to, PATIENT_E164)
        self.assertTrue(msg.startswith("BloodBridge:"))

    # ── Step 5: Multiple requests accumulate in-app notifications ────────
    def test_step5_patient_dashboard_shows_all_notifications(self):
        """Patient dashboard accumulates multiple in-app notifications."""

        for i in range(4):
            req = BloodRequest.objects.create(
                patient=self.patient,
                patient_name=self.patient.get_name,
                patient_age=self.patient.age,
                reason=f"Test request #{i}",
                bloodgroup="O-",
                unit=50,
                status="Pending",
            )
            _create_inapp_notification_safe(
                patient=self.patient,
                title=f"Request #{req.id} Submitted",
                message=f"Your request #{req.id} has been submitted.",
                related_request=req,
            )

        notifs = InAppNotification.objects.filter(patient=self.patient)
        self.assertEqual(notifs.count(), 4)
        # Most recent first
        titles = list(notifs.order_by("-created_at").values_list("title", flat=True))
        self.assertEqual(len(titles), 4)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 2 — Donor journey: signup → matched → donate → notifications
# ═══════════════════════════════════════════════════════════════════════════════
@override_settings(**SMS_SETTINGS)
class DonorJourneyTest(TestCase):
    """Simulate a real donor's complete lifecycle."""

    def setUp(self):
        _seed_stock()

        # Donor signs up (mirrors donorsignup_view)
        self.donor_user = User.objects.create_user(
            username="test_donor",
            password="TestDonor@123",
            first_name="Ravi",
            last_name="Kumar",
        )
        Group.objects.get_or_create(name="DONOR")
        self.donor_user.groups.add(Group.objects.get(name="DONOR"))

        self.donor = Donor.objects.create(
            user=self.donor_user,
            bloodgroup="O-",
            address="Madurai, TN 625001",
            mobile=DONOR_PHONE,
            zipcode="625001",
            is_available=True,
        )
        _add_valid_medical_report(self.donor)

        # A patient who will make requests
        self.patient_user = User.objects.create_user(
            username="test_patient",
            password="TestPatient@123",
            first_name="Josephin",
            last_name="Mary",
        )
        Group.objects.get_or_create(name="PATIENT")
        self.patient_user.groups.add(Group.objects.get(name="PATIENT"))

        self.patient = Patient.objects.create(
            user=self.patient_user,
            age=28,
            bloodgroup="O-",
            disease="Thalassemia",
            doctorname="Dr Priya",
            address="Madurai, TN 625002",
            mobile=PATIENT_PHONE,
        )

    # ── Step 1: Donor gets matched to urgent request ─────────────────────
    def test_step1_donor_receives_urgent_broadcast_sms(self):
        """Patient creates urgent request → donor gets broadcast SMS."""

        request = BloodRequest.objects.create(
            patient=self.patient,
            patient_name=self.patient.get_name,
            patient_age=self.patient.age,
            reason="Emergency surgery needs O-",
            bloodgroup="O-",
            unit=300,
            status="Pending",
            is_urgent=True,
            request_zipcode="625001",
        )

        mock_sns = MagicMock()
        result = sms_service.notify_matched_donors(
            request,
            contact_number=self.patient.mobile,
            sns_client=mock_sns,
        )

        self.assertTrue(result.enabled)
        self.assertEqual(result.delivered, 1)
        self.assertEqual(result.recipients[0], DONOR_E164)

    # ── Step 2: Donor makes a blood donation ─────────────────────────────
    def test_step2_donor_donates_and_admin_approves(self):
        """Donor donates blood → admin approves → donor gets SMS + in-app."""

        donation = BloodDonate.objects.create(
            donor=self.donor,
            disease="None",
            age=32,
            bloodgroup="O-",
            unit=350,
            status="Pending",
        )

        # Admin approves (mimics approve_donation_view)
        stock = Stock.objects.get(bloodgroup="O-")
        old_stock = stock.unit
        stock.unit += donation.unit
        stock.save()
        donation.status = "Approved"
        donation.save()

        # In-app notification
        _create_inapp_notification_safe(
            donor=self.donor,
            title="Donation Approved",
            message=f"Your donation #{donation.id} of 350ml (O-) has been approved.",
        )
        notif = InAppNotification.objects.filter(
            donor=self.donor, title="Donation Approved"
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn("approved", notif.message.lower())

        # SMS: donation approved
        mock_sender = MagicMock(return_value={"status": "success"})
        result = sms_service.notify_donation_approved(donation, sms_sender=mock_sender)
        self.assertEqual(result["status"], "success")
        to, msg = mock_sender.call_args.args
        self.assertEqual(to, DONOR_E164)
        self.assertTrue(msg.startswith("BloodBridge:"))
        self.assertIn(str(donation.id), msg)

        # Stock correctly incremented
        stock.refresh_from_db()
        self.assertEqual(stock.unit, old_stock + 350)

    # ── Step 3: Donor's donation gets rejected ───────────────────────────
    def test_step3_donor_donation_rejected(self):
        """Donor donates blood → admin rejects → donor gets SMS + in-app."""

        donation = BloodDonate.objects.create(
            donor=self.donor,
            disease="Mild fever",
            age=32,
            bloodgroup="O-",
            unit=200,
            status="Pending",
        )

        donation.status = "Rejected"
        donation.save()

        # In-app notification
        _create_inapp_notification_safe(
            donor=self.donor,
            title="Donation Rejected",
            message=f"Your donation #{donation.id} of 200ml (O-) was rejected.",
        )
        notif = InAppNotification.objects.filter(
            donor=self.donor, title="Donation Rejected"
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn("rejected", notif.message.lower())

        # SMS: donation rejected
        mock_sender = MagicMock(return_value={"status": "success"})
        result = sms_service.notify_donation_rejected(
            donation, reason="Donor had fever at time of donation", sms_sender=mock_sender
        )
        self.assertEqual(result["status"], "success")
        to, msg = mock_sender.call_args.args
        self.assertEqual(to, DONOR_E164)
        self.assertTrue(msg.startswith("BloodBridge:"))

    # ── Step 4: Donor gets matched as top donor on approved request ──────
    def test_step4_donor_gets_matched_on_approved_request(self):
        """Admin approves request → donor gets 'top match' SMS + in-app."""

        request = BloodRequest.objects.create(
            patient=self.patient,
            patient_name=self.patient.get_name,
            patient_age=self.patient.age,
            reason="Need O- blood for transfusion",
            bloodgroup="O-",
            unit=100,
            status="Approved",
            is_urgent=False,
            request_zipcode="625001",
        )

        # In-app notification for donor match
        _create_inapp_notification_safe(
            donor=self.donor,
            title="New Approved Request Match",
            message=f"You are a top match for approved request #{request.id} (O-, 100ml).",
            related_request=request,
        )
        notif = InAppNotification.objects.filter(
            donor=self.donor, title="New Approved Request Match"
        ).first()
        self.assertIsNotNone(notif)
        self.assertEqual(notif.related_request_id, request.id)

        # SMS: approval sends to both patient and donor
        mock_sender = MagicMock(return_value={"status": "success"})
        result = sms_service.notify_request_approved(request, sms_sender=mock_sender)
        self.assertEqual(result["status"], "sent")

        sms_phones = {c.args[0] for c in mock_sender.call_args_list}
        self.assertIn(DONOR_E164, sms_phones)

    # ── Step 5: Donor dashboard shows accumulated notifications ──────────
    def test_step5_donor_dashboard_notifications_accumulate(self):
        """Multiple events → donor dashboard shows all in-app notifications."""

        # Urgent broadcast in-app
        _create_inapp_notification_safe(
            donor=self.donor,
            title="Urgent Blood Request Nearby",
            message="Patient nearby needs O- blood urgently.",
        )

        # Donation approved in-app
        _create_inapp_notification_safe(
            donor=self.donor,
            title="Donation Approved",
            message="Your donation of 350ml (O-) was approved.",
        )

        # Match notification in-app
        _create_inapp_notification_safe(
            donor=self.donor,
            title="New Approved Request Match",
            message="You are matched to a new approved request.",
        )

        notifs = InAppNotification.objects.filter(donor=self.donor)
        self.assertEqual(notifs.count(), 3)
        # Should be ordered newest first
        titles = list(notifs.order_by("-created_at").values_list("title", flat=True))
        self.assertIn("Donation Approved", titles)
        self.assertIn("New Approved Request Match", titles)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 3 — Full lifecycle: patient + donor interacting through a single request
# ═══════════════════════════════════════════════════════════════════════════════
@override_settings(**SMS_SETTINGS)
class FullLifecycleTest(TestCase):
    """End-to-end lifecycle: patient requests → donor matched → admin actions.

    Simulates a real-world scenario where:
    1. Patient Josephin (9385425650) submits urgent O- blood request
    2. Donor Ravi (9361046558) gets urgent broadcast SMS
    3. Patient gets requester confirmation SMS
    4. Admin approves the request
    5. Patient gets approval SMS + in-app, donor gets match SMS + in-app
    6. Donor donates blood → admin approves → donor gets donation approval SMS
    7. Verify all in-app notifications are present for both users
    """

    def setUp(self):
        _seed_stock()

        self.patient_user = User.objects.create_user(
            username="josephin", password="Pass@1234",
            first_name="Josephin", last_name="Mary",
        )
        self.patient = Patient.objects.create(
            user=self.patient_user, age=28, bloodgroup="O-",
            disease="Anaemia", doctorname="Dr Ramesh",
            address="Chennai", mobile=PATIENT_PHONE,
        )
        Group.objects.get_or_create(name="PATIENT")
        self.patient_user.groups.add(Group.objects.get(name="PATIENT"))

        self.donor_user = User.objects.create_user(
            username="ravi_donor", password="Pass@1234",
            first_name="Ravi", last_name="Kumar",
        )
        self.donor = Donor.objects.create(
            user=self.donor_user, bloodgroup="O-",
            address="Chennai", mobile=DONOR_PHONE,
            zipcode="600001", is_available=True,
        )
        _add_valid_medical_report(self.donor)
        Group.objects.get_or_create(name="DONOR")
        self.donor_user.groups.add(Group.objects.get(name="DONOR"))

    def test_full_lifecycle_all_notifications(self):
        mock_sender = MagicMock(return_value={"status": "success"})
        mock_sns = MagicMock()

        # ── STEP 1: Patient submits urgent request ──────────────────────
        request = BloodRequest.objects.create(
            patient=self.patient,
            patient_name="Josephin Mary",
            patient_age=28,
            reason="Emergency O- needed for surgery",
            bloodgroup="O-",
            unit=200,
            status="Pending",
            is_urgent=True,
            request_zipcode="600001",
        )
        _create_inapp_notification_safe(
            patient=self.patient,
            title="Blood Request Submitted",
            message=f"Your request #{request.id} for O- (200ml) is pending.",
            related_request=request,
        )

        # ── STEP 2: System sends urgent broadcast to donor ──────────────
        broadcast = sms_service.notify_matched_donors(
            request, contact_number=PATIENT_PHONE, sns_client=mock_sns,
        )
        self.assertEqual(broadcast.delivered, 1)
        self.assertIn(DONOR_E164, broadcast.recipients)

        # ── STEP 3: System sends requester confirmation to patient ──────
        confirm = sms_service.send_requester_confirmation(
            request, PATIENT_PHONE, sms_sender=mock_sender,
        )
        self.assertEqual(confirm["status"], "success")
        self.assertEqual(mock_sender.call_args.args[0], PATIENT_E164)

        # ── STEP 4: Admin approves the request ──────────────────────────
        stock = Stock.objects.get(bloodgroup="O-")
        stock.unit -= request.unit
        stock.save()
        request.status = "Approved"
        request.save()

        # Patient in-app
        _notify_request_owner_inapp(
            request,
            title="Blood Request Approved",
            message=f"Your request #{request.id} for O- (200ml) has been approved.",
        )
        # Donor in-app
        _create_inapp_notification_safe(
            donor=self.donor,
            title="New Approved Request Match",
            message=f"You are a top match for request #{request.id} (O-, 200ml).",
            related_request=request,
        )

        # ── STEP 5: Approval SMS to both ────────────────────────────────
        mock_sender.reset_mock()
        approval = sms_service.notify_request_approved(request, sms_sender=mock_sender)
        self.assertEqual(approval["status"], "sent")
        self.assertEqual(mock_sender.call_count, 2)
        phones = {c.args[0] for c in mock_sender.call_args_list}
        self.assertIn(PATIENT_E164, phones)
        self.assertIn(DONOR_E164, phones)

        # ── STEP 6: Donor donates blood ─────────────────────────────────
        donation = BloodDonate.objects.create(
            donor=self.donor, disease="None", age=30,
            bloodgroup="O-", unit=350, status="Pending",
        )

        # Admin approves donation
        stock.refresh_from_db()
        stock.unit += donation.unit
        stock.save()
        donation.status = "Approved"
        donation.save()

        _create_inapp_notification_safe(
            donor=self.donor,
            title="Donation Approved",
            message=f"Your donation #{donation.id} of 350ml (O-) has been approved. Thank you!",
        )

        mock_sender.reset_mock()
        don_result = sms_service.notify_donation_approved(donation, sms_sender=mock_sender)
        self.assertEqual(don_result["status"], "success")
        self.assertEqual(mock_sender.call_args.args[0], DONOR_E164)

        # ── VERIFY: All in-app notifications for both users ─────────────
        patient_notifs = InAppNotification.objects.filter(patient=self.patient).order_by("-created_at")
        donor_notifs = InAppNotification.objects.filter(donor=self.donor).order_by("-created_at")

        self.assertEqual(patient_notifs.count(), 2)  # Submitted + Approved
        self.assertEqual(donor_notifs.count(), 2)  # Match + Donation Approved

        patient_titles = set(patient_notifs.values_list("title", flat=True))
        donor_titles = set(donor_notifs.values_list("title", flat=True))

        self.assertEqual(patient_titles, {"Blood Request Submitted", "Blood Request Approved"})
        self.assertEqual(donor_titles, {"New Approved Request Match", "Donation Approved"})


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 4 — Edge cases & error handling in notification flows
# ═══════════════════════════════════════════════════════════════════════════════
@override_settings(**SMS_SETTINGS)
class NotificationEdgeCaseTest(TestCase):
    """Test edge cases that could occur in real usage."""

    def setUp(self):
        _seed_stock()
        self.patient_user = User.objects.create_user(
            username="edge_patient", password="Edge@1234",
            first_name="Edge", last_name="Patient",
        )
        self.patient = Patient.objects.create(
            user=self.patient_user, age=25, bloodgroup="B+",
            disease="Test", doctorname="Dr Test",
            address="Test City", mobile=PATIENT_PHONE,
        )

    def test_non_urgent_request_skips_donor_broadcast(self):
        """Non-urgent request should NOT trigger donor broadcast SMS."""
        request = BloodRequest.objects.create(
            patient=self.patient,
            patient_name=self.patient.get_name,
            patient_age=25,
            reason="Routine transfusion",
            bloodgroup="B+",
            unit=100,
            status="Pending",
            is_urgent=False,
        )

        mock_sns = MagicMock()
        result = sms_service.notify_matched_donors(
            request, contact_number=PATIENT_PHONE, sns_client=mock_sns,
        )
        self.assertEqual(result.attempted, 0)
        self.assertEqual(result.reason, "not-urgent")
        mock_sns.publish.assert_not_called()

    @override_settings(AWS_SNS_ENABLED=False)
    def test_sms_disabled_skips_all_sms(self):
        """When AWS_SNS_ENABLED=False, all SMS functions return 'skipped'."""
        request = BloodRequest.objects.create(
            patient=self.patient,
            patient_name=self.patient.get_name,
            patient_age=25,
            reason="Test",
            bloodgroup="B+",
            unit=50,
            status="Pending",
            is_urgent=True,
        )

        # Urgent broadcast
        result = sms_service.notify_matched_donors(request, contact_number=PATIENT_PHONE)
        self.assertFalse(result.enabled)
        self.assertEqual(result.reason, "sns-disabled")

        # Requester confirmation
        resp = sms_service.send_requester_confirmation(request, PATIENT_PHONE)
        self.assertEqual(resp["status"], "skipped")
        self.assertEqual(resp["reason"], "sns-disabled")

        # Approval
        result = sms_service.notify_request_approved(request)
        self.assertEqual(result["status"], "skipped")

        # Rejection
        result = sms_service.notify_request_rejected(request, reason="test")
        self.assertEqual(result["status"], "skipped")

    def test_patient_with_no_phone_skips_sms_gracefully(self):
        """Patient with empty mobile should skip SMS without errors."""
        no_phone_user = User.objects.create_user(
            username="no_phone", password="NoPhone@1",
            first_name="No", last_name="Phone",
        )
        no_phone_patient = Patient.objects.create(
            user=no_phone_user, age=30, bloodgroup="A+",
            disease="Test", doctorname="Dr Test",
            address="Test", mobile="",
        )
        request = BloodRequest.objects.create(
            patient=no_phone_patient,
            patient_name="No Phone",
            patient_age=30,
            reason="Test",
            bloodgroup="A+",
            unit=50,
            status="Pending",
        )

        resp = sms_service.send_requester_confirmation(request, "")
        self.assertEqual(resp["status"], "skipped")
        self.assertEqual(resp["reason"], "no-contact")

    def test_donor_cooldown_prevents_repeated_broadcast(self):
        """Donor who was recently notified should be skipped by cooldown."""
        donor_user = User.objects.create_user(
            username="cooldown_donor", password="Cool@1234",
            first_name="Cool", last_name="Donor",
        )
        donor = Donor.objects.create(
            user=donor_user, bloodgroup="B+",
            address="Test", mobile=DONOR_PHONE,
            zipcode="600001", is_available=True,
            last_notified_at=timezone.now(),  # Just notified!
        )

        request = BloodRequest.objects.create(
            patient=self.patient,
            patient_name=self.patient.get_name,
            patient_age=25,
            reason="Urgent B+",
            bloodgroup="B+",
            unit=100,
            status="Pending",
            is_urgent=True,
            request_zipcode="600001",
        )

        # With a 60-second cooldown, donor should be skipped
        with self.settings(AWS_SNS_MIN_NOTIFICATION_GAP_SECONDS=60):
            mock_sns = MagicMock()
            result = sms_service.notify_matched_donors(
                request, contact_number=PATIENT_PHONE, sns_client=mock_sns,
            )
            self.assertEqual(result.delivered, 0)
            self.assertEqual(result.reason, "no-donors")
            mock_sns.publish.assert_not_called()

    def test_donor_with_invalid_phone_skips_donation_sms(self):
        """Donor with invalid/empty phone number → donation SMS should be skipped."""
        bad_user = User.objects.create_user(
            username="bad_phone_donor", password="Bad@1234",
            first_name="Bad", last_name="Phone",
        )
        bad_donor = Donor.objects.create(
            user=bad_user, bloodgroup="AB-",
            address="Nowhere", mobile="",
            zipcode="999999", is_available=True,
        )

        donation = BloodDonate.objects.create(
            donor=bad_donor, disease="None", age=30,
            bloodgroup="AB-", unit=150, status="Approved",
        )

        mock_sender = MagicMock(return_value={"status": "success"})
        result = sms_service.notify_donation_approved(donation, sms_sender=mock_sender)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no-donor-contact")
        mock_sender.assert_not_called()

    def test_phone_normalization(self):
        """Phone numbers in various formats should normalize to E.164."""
        cases = [
            ("9385425650", "+919385425650"),
            ("09385425650", "+919385425650"),
            ("+919385425650", "+919385425650"),
            ("919385425650", "+919385425650"),
            ("9361046558", "+919361046558"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_phone_number(raw), expected)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 5 — Duplicate donor profiles (same phone → multiple records)
# ═══════════════════════════════════════════════════════════════════════════════
@override_settings(**SMS_SETTINGS)
class DuplicateDonorPhoneTest(TestCase):
    """When multiple donor records share the same phone, all should get in-app."""

    def setUp(self):
        _seed_stock()
        self.patient_user = User.objects.create_user(
            username="dup_patient", password="Dup@1234",
            first_name="Dup", last_name="Patient",
        )
        self.patient = Patient.objects.create(
            user=self.patient_user, age=25, bloodgroup="O-",
            disease="Test", doctorname="Dr Dup",
            address="Test City", mobile=PATIENT_PHONE,
        )

        # Two donor records sharing the same phone number
        self.donor_user1 = User.objects.create_user(
            username="donor_dup1", password="Dup@1234",
            first_name="Donor", last_name="One",
        )
        self.donor1 = Donor.objects.create(
            user=self.donor_user1, bloodgroup="O-",
            address="Test", mobile=DONOR_PHONE,
            zipcode="600001", is_available=True,
        )
        _add_valid_medical_report(self.donor1)

        self.donor_user2 = User.objects.create_user(
            username="donor_dup2", password="Dup@1234",
            first_name="Donor", last_name="Two",
        )
        self.donor2 = Donor.objects.create(
            user=self.donor_user2, bloodgroup="O-",
            address="Test", mobile=DONOR_PHONE,  # Same phone!
            zipcode="600001", is_available=True,
        )
        _add_valid_medical_report(self.donor2)

    def test_both_donors_get_inapp_notification(self):
        """Both donor records with the same phone get in-app notifications."""
        request = BloodRequest.objects.create(
            patient=self.patient,
            patient_name=self.patient.get_name,
            patient_age=25,
            reason="Test",
            bloodgroup="O-",
            unit=100,
            status="Approved",
        )

        # Simulate what the approve view does: find all donors with matching phone
        top_phone = DONOR_E164
        last_digits = top_phone.lstrip("+")[-10:]
        candidates = Donor.objects.filter(mobile__icontains=last_digits)

        for donor in candidates:
            if normalize_phone_number(donor.mobile) == top_phone:
                _create_inapp_notification_safe(
                    donor=donor,
                    title="New Approved Request Match",
                    message=f"You are a top match for request #{request.id}.",
                    related_request=request,
                )

        # Both donors should have the notification
        for donor in [self.donor1, self.donor2]:
            notif = InAppNotification.objects.filter(
                donor=donor, title="New Approved Request Match"
            ).first()
            self.assertIsNotNone(
                notif,
                f"Donor {donor.user.username} (id={donor.id}) did not receive in-app notification",
            )

    def test_broadcast_sms_deduplicates_phone(self):
        """Even with duplicate records, broadcast SMS only sends once per phone."""
        request = BloodRequest.objects.create(
            patient=self.patient,
            patient_name=self.patient.get_name,
            patient_age=25,
            reason="Urgent O- needed",
            bloodgroup="O-",
            unit=100,
            status="Pending",
            is_urgent=True,
            request_zipcode="600001",
        )

        mock_sns = MagicMock()
        result = sms_service.notify_matched_donors(
            request, contact_number=PATIENT_PHONE, sns_client=mock_sns,
        )

        # Should deduplicate: only 1 SMS despite 2 donor records with same phone
        self.assertEqual(result.delivered, 1)
        self.assertEqual(len(result.recipients), 1)
        self.assertEqual(result.recipients[0], DONOR_E164)
        self.assertEqual(mock_sns.publish.call_count, 1)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 6 — SMS message content validation (India DLT-safe templates)
# ═══════════════════════════════════════════════════════════════════════════════
@override_settings(**SMS_SETTINGS)
class SMSTemplateContentTest(TestCase):
    """Validate that SMS messages are India-safe (short, start with 'BloodBridge:')."""

    def setUp(self):
        _seed_stock()
        self.patient_user = User.objects.create_user(
            username="tmpl_patient", password="Tmpl@1234",
            first_name="Template", last_name="Patient",
        )
        self.patient = Patient.objects.create(
            user=self.patient_user, age=30, bloodgroup="AB+",
            disease="Test", doctorname="Dr Template",
            address="Test City", mobile=PATIENT_PHONE,
        )
        self.donor_user = User.objects.create_user(
            username="tmpl_donor", password="Tmpl@1234",
            first_name="Template", last_name="Donor",
        )
        self.donor = Donor.objects.create(
            user=self.donor_user, bloodgroup="AB+",
            address="Test", mobile=DONOR_PHONE,
            zipcode="600001", is_available=True,
        )
        _add_valid_medical_report(self.donor)

    def test_approval_message_india_template(self):
        """Approval SMS messages use short India-safe templates."""
        request = BloodRequest.objects.create(
            patient=self.patient,
            patient_name=self.patient.get_name,
            patient_age=30,
            reason="Need AB+",
            bloodgroup="AB+",
            unit=150,
            status="Approved",
            request_zipcode="600001",
        )

        mock_sender = MagicMock(return_value={"status": "success"})
        sms_service.notify_request_approved(request, sms_sender=mock_sender)

        for c in mock_sender.call_args_list:
            msg = c.args[1]
            self.assertTrue(msg.startswith("BloodBridge:"), f"Message doesn't start with 'BloodBridge:': {msg}")
            self.assertLessEqual(len(msg), 160, f"Message too long ({len(msg)} chars): {msg}")

    def test_rejection_message_india_template(self):
        """Rejection SMS uses short India-safe template."""
        request = BloodRequest.objects.create(
            patient=self.patient,
            patient_name=self.patient.get_name,
            patient_age=30,
            reason="Test",
            bloodgroup="AB+",
            unit=100,
            status="Rejected",
        )

        mock_sender = MagicMock(return_value={"status": "success"})
        sms_service.notify_request_rejected(
            request, reason="Insufficient stock", sms_sender=mock_sender
        )

        msg = mock_sender.call_args.args[1]
        self.assertTrue(msg.startswith("BloodBridge:"))
        self.assertLessEqual(len(msg), 160)

    def test_donation_approval_message_india_template(self):
        """Donation approved SMS uses short India-safe template."""
        donation = BloodDonate.objects.create(
            donor=self.donor, disease="None", age=30,
            bloodgroup="AB+", unit=200, status="Approved",
        )

        mock_sender = MagicMock(return_value={"status": "success"})
        sms_service.notify_donation_approved(donation, sms_sender=mock_sender)

        msg = mock_sender.call_args.args[1]
        self.assertTrue(msg.startswith("BloodBridge:"))
        self.assertLessEqual(len(msg), 160)
        self.assertIn(str(donation.id), msg)

    def test_donation_rejection_message_india_template(self):
        """Donation rejected SMS uses short India-safe template."""
        donation = BloodDonate.objects.create(
            donor=self.donor, disease="None", age=30,
            bloodgroup="AB+", unit=200, status="Rejected",
        )

        mock_sender = MagicMock(return_value={"status": "success"})
        sms_service.notify_donation_rejected(
            donation, reason="Test rejection", sms_sender=mock_sender
        )

        msg = mock_sender.call_args.args[1]
        self.assertTrue(msg.startswith("BloodBridge:"))
        self.assertLessEqual(len(msg), 160)

    def test_requester_confirmation_contains_request_id(self):
        """Requester confirmation SMS includes the request ID."""
        request = BloodRequest.objects.create(
            patient=self.patient,
            patient_name=self.patient.get_name,
            patient_age=30,
            reason="Test",
            bloodgroup="AB+",
            unit=50,
            status="Pending",
        )

        mock_sender = MagicMock(return_value={"status": "success"})
        sms_service.send_requester_confirmation(
            request, PATIENT_PHONE, sms_sender=mock_sender
        )

        msg = mock_sender.call_args.args[1]
        self.assertIn(str(request.id), msg)
        self.assertIn("BloodBridge", msg)
