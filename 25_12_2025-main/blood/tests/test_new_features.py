"""
Comprehensive A-to-Z tests for all newly added features:
  - Aadhaar number validation (donor + patient)
  - Medical report model, upload, 90-day validity
  - Doctor prescription (patient signup + blood request)
  - Admin approval workflow (approve/reject donor & patient)
  - Login blocking for unapproved accounts
  - Welcome notification + SMS on approval
  - Medical report upload/renewal view
  - Management command: send_medical_report_reminders
  - Donor recommender penalty for expired reports
  - URL routing for all new endpoints
  - Template rendering for all new pages
"""

import os
import io
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.conf import settings
from django.contrib.auth.models import User, Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, Client, override_settings
from django.urls import reverse, resolve
from django.utils import timezone

from blood.models import BloodRequest, Stock, InAppNotification
from donor.models import Donor, MedicalReport, MEDICAL_REPORT_VALIDITY_DAYS
from donor.forms import DonorForm, DonorUserForm, MedicalReportForm
from patient.models import Patient
from patient.forms import PatientForm, PatientUserForm


# ---------------------------------------------------------------------------
# Helper mixins / factories
# ---------------------------------------------------------------------------

def _fake_pdf(name='report.pdf', size=1024):
    """Create a tiny fake PDF for file upload tests."""
    return SimpleUploadedFile(name, b'%PDF-1.4 fake content ' * (size // 20 + 1),
                              content_type='application/pdf')


def _fake_image(name='photo.jpg'):
    """Create a minimal JPEG-like file for image upload fields."""
    return SimpleUploadedFile(name, b'\xff\xd8\xff\xe0' + b'\x00' * 100,
                              content_type='image/jpeg')


class _ApprovedDonorMixin:
    """Creates a fully approved donor user ready for login."""

    def _make_approved_donor(self, username='donor1', password='Str0ng!Pass99',
                              bloodgroup='O+', mobile='+919385425650',
                              aadhaar='123456789012'):
        user = User.objects.create_user(username=username, password=password,
                                         first_name='Test', last_name='Donor')
        user.is_active = True
        user.save()
        group, _ = Group.objects.get_or_create(name='DONOR')
        group.user_set.add(user)
        donor = Donor.objects.create(
            user=user, bloodgroup=bloodgroup, address='Test Addr',
            mobile=mobile, aadhaar_number=aadhaar, is_approved=True,
            approved_at=timezone.now(),
        )
        return user, donor


class _ApprovedPatientMixin:
    """Creates a fully approved patient user ready for login."""

    def _make_approved_patient(self, username='patient1', password='Str0ng!Pass99',
                                bloodgroup='A+', mobile='+919385425650',
                                aadhaar='987654321012'):
        user = User.objects.create_user(username=username, password=password,
                                         first_name='Test', last_name='Patient')
        user.is_active = True
        user.save()
        group, _ = Group.objects.get_or_create(name='PATIENT')
        group.user_set.add(user)
        patient = Patient.objects.create(
            user=user, age=30, bloodgroup=bloodgroup, disease='None',
            doctorname='Dr. Test', address='Test Addr', mobile=mobile,
            aadhaar_number=aadhaar, is_approved=True, approved_at=timezone.now(),
        )
        return user, patient


# ===========================================================================
# 1. AADHAAR VALIDATION TESTS
# ===========================================================================

class AadhaarDonorFormTests(TestCase):
    """Aadhaar field validation on DonorForm."""

    BASE = {
        'bloodgroup': 'A+', 'address': '123 St', 'mobile': '9385425650', 'sex': 'U',
    }

    def test_valid_12_digit_aadhaar(self):
        data = {**self.BASE, 'aadhaar_number': '123456789012'}
        form = DonorForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_aadhaar_too_short(self):
        data = {**self.BASE, 'aadhaar_number': '12345'}
        form = DonorForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn('aadhaar_number', form.errors)

    def test_aadhaar_too_long(self):
        data = {**self.BASE, 'aadhaar_number': '1234567890123'}
        form = DonorForm(data=data)
        self.assertFalse(form.is_valid())

    def test_aadhaar_with_letters(self):
        data = {**self.BASE, 'aadhaar_number': '12345678ABCD'}
        form = DonorForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn('aadhaar_number', form.errors)

    def test_aadhaar_empty_default(self):
        """Empty string is the model default—form should reject it (regex \\d{12})."""
        data = {**self.BASE, 'aadhaar_number': ''}
        form = DonorForm(data=data)
        self.assertFalse(form.is_valid())


class AadhaarPatientFormTests(TestCase):
    """Aadhaar field validation on PatientForm."""

    BASE = {
        'age': 30, 'bloodgroup': 'B+', 'disease': 'None',
        'doctorname': 'Dr. X', 'address': '456 St', 'mobile': '9385425650',
    }

    def test_valid_aadhaar(self):
        data = {**self.BASE, 'aadhaar_number': '111122223333'}
        form = PatientForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_invalid_aadhaar_alpha(self):
        data = {**self.BASE, 'aadhaar_number': 'ABCDEFGHIJKL'}
        form = PatientForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn('aadhaar_number', form.errors)

    def test_aadhaar_special_chars(self):
        data = {**self.BASE, 'aadhaar_number': '1234-5678-9012'}
        form = PatientForm(data=data)
        self.assertFalse(form.is_valid())


# ===========================================================================
# 2. MEDICAL REPORT MODEL + VALIDITY TESTS
# ===========================================================================

class MedicalReportModelTests(TestCase, _ApprovedDonorMixin):
    """Test MedicalReport model properties and 90-day validity logic."""

    def setUp(self):
        self.user, self.donor = self._make_approved_donor()

    def test_no_report_means_invalid(self):
        self.assertFalse(self.donor.is_medical_report_valid)
        self.assertIsNone(self.donor.latest_medical_report)
        self.assertIsNone(self.donor.medical_report_expiry_date)
        self.assertIsNone(self.donor.medical_report_days_remaining)

    def test_fresh_report_is_valid(self):
        report = MedicalReport.objects.create(
            donor=self.donor, document=_fake_pdf(), document_name='report.pdf',
        )
        self.assertTrue(self.donor.is_medical_report_valid)
        self.assertIsNotNone(self.donor.medical_report_expiry_date)
        self.assertFalse(report.is_expired)

    def test_report_expires_after_90_days(self):
        report = MedicalReport.objects.create(
            donor=self.donor, document=_fake_pdf(), document_name='old.pdf',
        )
        # Manually backdate the uploaded_at
        past = timezone.now() - timedelta(days=MEDICAL_REPORT_VALIDITY_DAYS + 1)
        MedicalReport.objects.filter(pk=report.pk).update(uploaded_at=past)
        report.refresh_from_db()

        self.assertTrue(report.is_expired)
        self.assertFalse(self.donor.is_medical_report_valid)
        self.assertLess(self.donor.medical_report_days_remaining, 0)

    def test_expiry_date_calculation(self):
        report = MedicalReport.objects.create(
            donor=self.donor, document=_fake_pdf(), document_name='r.pdf',
        )
        expected = (report.uploaded_at + timedelta(days=MEDICAL_REPORT_VALIDITY_DAYS)).date()
        self.assertEqual(self.donor.medical_report_expiry_date, expected)

    def test_days_remaining_positive(self):
        MedicalReport.objects.create(
            donor=self.donor, document=_fake_pdf(), document_name='r.pdf',
        )
        remaining = self.donor.medical_report_days_remaining
        self.assertIsNotNone(remaining)
        self.assertGreater(remaining, 80)  # just uploaded → ~90 days

    def test_latest_report_returns_newest(self):
        old = MedicalReport.objects.create(
            donor=self.donor, document=_fake_pdf('old.pdf'), document_name='old.pdf',
        )
        past = timezone.now() - timedelta(days=30)
        MedicalReport.objects.filter(pk=old.pk).update(uploaded_at=past)

        new = MedicalReport.objects.create(
            donor=self.donor, document=_fake_pdf('new.pdf'), document_name='new.pdf',
        )
        self.assertEqual(self.donor.latest_medical_report.pk, new.pk)

    def test_str_representation(self):
        report = MedicalReport.objects.create(
            donor=self.donor, document=_fake_pdf(), document_name='report.pdf',
        )
        self.assertIn('Medical Report for', str(report))
        self.assertIn(self.donor.get_name, str(report))


# ===========================================================================
# 3. MEDICAL REPORT FORM VALIDATION TESTS
# ===========================================================================

class MedicalReportFormTests(TestCase):
    """MedicalReportForm file type / size validation."""

    def test_valid_pdf_upload(self):
        form = MedicalReportForm(data={'notes': ''}, files={'document': _fake_pdf()})
        self.assertTrue(form.is_valid(), form.errors)

    def test_valid_jpg_upload(self):
        form = MedicalReportForm(data={'notes': ''}, files={'document': _fake_image()})
        self.assertTrue(form.is_valid(), form.errors)

    def test_rejects_unsupported_extension(self):
        bad_file = SimpleUploadedFile('report.exe', b'MZ' + b'\x00' * 100,
                                      content_type='application/octet-stream')
        form = MedicalReportForm(data={'notes': ''}, files={'document': bad_file})
        self.assertFalse(form.is_valid())
        self.assertIn('document', form.errors)

    def test_rejects_oversized_file(self):
        huge = SimpleUploadedFile('big.pdf', b'%PDF-1.4 ' * (11 * 1024 * 1024 // 9 + 1),
                                  content_type='application/pdf')
        form = MedicalReportForm(data={'notes': ''}, files={'document': huge})
        self.assertFalse(form.is_valid())
        self.assertIn('document', form.errors)


# ===========================================================================
# 4. DONOR SIGNUP WITH MEDICAL REPORT
# ===========================================================================

@override_settings(GEOCODER_ALLOW_REMOTE=False)
class DonorSignupTests(TestCase):
    """Donor registration now requires Aadhaar + medical report."""

    SIGNUP_URL = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.SIGNUP_URL = reverse('donorsignup')

    def _valid_signup_data(self):
        return {
            'first_name': 'New', 'last_name': 'Donor',
            'username': 'newdonor', 'password': 'Str0ng!Pass99',
            'aadhaar_number': '123456789012',
            'bloodgroup': 'A+', 'address': '100 Test St',
            'mobile': '9385425650', 'sex': 'M',
        }

    def test_signup_page_renders(self):
        resp = self.client.get(self.SIGNUP_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Aadhaar')  # new field visible

    def test_signup_missing_medical_report(self):
        """Submit without uploading medical report file → stay on form with error."""
        resp = self.client.post(self.SIGNUP_URL, self._valid_signup_data())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Medical health report is mandatory')
        self.assertFalse(User.objects.filter(username='newdonor').exists())

    @override_settings(GEOCODER_ALLOW_REMOTE=False)
    def test_signup_success_creates_inactive_user(self):
        """Full valid signup → user.is_active=False, donor.is_approved=False, redirect."""
        data = self._valid_signup_data()
        files = {'document': _fake_pdf()}
        resp = self.client.post(self.SIGNUP_URL, {**data, **files})
        self.assertEqual(resp.status_code, 302)
        self.assertIn('pending-approval', resp.url)

        user = User.objects.get(username='newdonor')
        self.assertFalse(user.is_active)
        self.assertTrue(user.groups.filter(name='DONOR').exists())

        donor = Donor.objects.get(user=user)
        self.assertFalse(donor.is_approved)
        self.assertEqual(donor.aadhaar_number, '123456789012')

        # Medical report saved
        self.assertEqual(MedicalReport.objects.filter(donor=donor).count(), 1)

    @override_settings(GEOCODER_ALLOW_REMOTE=False)
    def test_signup_creates_registration_notification(self):
        data = self._valid_signup_data()
        files = {'document': _fake_pdf()}
        self.client.post(self.SIGNUP_URL, {**data, **files})

        donor = Donor.objects.get(user__username='newdonor')
        notifs = InAppNotification.objects.filter(donor=donor)
        self.assertTrue(notifs.exists())
        self.assertIn('Registration Received', notifs.first().title)

    def test_signup_invalid_aadhaar_stays_on_form(self):
        data = self._valid_signup_data()
        data['aadhaar_number'] = '12345'  # too short
        files = {'document': _fake_pdf()}
        resp = self.client.post(self.SIGNUP_URL, {**data, **files})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username='newdonor').exists())


# ===========================================================================
# 5. DONOR PENDING APPROVAL VIEW
# ===========================================================================

class DonorPendingApprovalViewTests(TestCase):
    """Test the donor pending approval waiting page."""

    def test_pending_page_renders(self):
        resp = self.client.get(reverse('donor-pending-approval'))
        self.assertEqual(resp.status_code, 200)


# ===========================================================================
# 6. PATIENT SIGNUP WITH PRESCRIPTION
# ===========================================================================

class PatientSignupNewTests(TestCase):
    """Patient registration now requires Aadhaar + doctor prescription."""

    SIGNUP_URL = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.SIGNUP_URL = reverse('patientsignup')

    def _valid_signup_data(self):
        return {
            'first_name': 'New', 'last_name': 'Patient',
            'username': 'newpatient', 'password': 'Str0ng!Pass99',
            'aadhaar_number': '999888777666',
            'age': 25, 'bloodgroup': 'B+', 'disease': 'Anemia',
            'doctorname': 'Dr. Smith', 'address': '200 Test St',
            'mobile': '9385425650',
        }

    def test_signup_page_renders_with_aadhaar(self):
        resp = self.client.get(self.SIGNUP_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Aadhaar')

    def test_signup_missing_prescription(self):
        """Submit without prescription → stay on form with error."""
        resp = self.client.post(self.SIGNUP_URL, self._valid_signup_data())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Doctor prescription is mandatory')
        self.assertFalse(User.objects.filter(username='newpatient').exists())

    def test_signup_success(self):
        data = self._valid_signup_data()
        files = {'doctor_prescription': _fake_pdf('prescription.pdf')}
        resp = self.client.post(self.SIGNUP_URL, {**data, **files})
        self.assertEqual(resp.status_code, 302)
        self.assertIn('pending-approval', resp.url)

        user = User.objects.get(username='newpatient')
        self.assertFalse(user.is_active)
        self.assertTrue(user.groups.filter(name='PATIENT').exists())

        patient = Patient.objects.get(user=user)
        self.assertFalse(patient.is_approved)
        self.assertEqual(patient.aadhaar_number, '999888777666')
        self.assertTrue(patient.doctor_prescription)  # file stored

    def test_signup_creates_registration_notification(self):
        data = self._valid_signup_data()
        files = {'doctor_prescription': _fake_pdf('prescription.pdf')}
        self.client.post(self.SIGNUP_URL, {**data, **files})

        patient = Patient.objects.get(user__username='newpatient')
        notifs = InAppNotification.objects.filter(patient=patient)
        self.assertTrue(notifs.exists())
        self.assertIn('Registration Received', notifs.first().title)

    def test_signup_invalid_aadhaar(self):
        data = self._valid_signup_data()
        data['aadhaar_number'] = 'ABCD'
        files = {'doctor_prescription': _fake_pdf()}
        resp = self.client.post(self.SIGNUP_URL, {**data, **files})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username='newpatient').exists())


# ===========================================================================
# 7. PATIENT PENDING APPROVAL VIEW
# ===========================================================================

class PatientPendingApprovalViewTests(TestCase):
    """Test the patient pending approval waiting page."""

    def test_pending_page_renders(self):
        resp = self.client.get(reverse('patient-pending-approval'))
        self.assertEqual(resp.status_code, 200)


# ===========================================================================
# 8. LOGIN BLOCKING FOR UNAPPROVED ACCOUNTS
# ===========================================================================

class DonorLoginBlockingTests(TestCase):
    """Unapproved donors cannot login even with correct credentials."""

    def setUp(self):
        self.user = User.objects.create_user('pendingdonor', password='Str0ng!Pass99',
                                              first_name='Pending', last_name='Donor')
        self.user.is_active = False
        self.user.save()
        group, _ = Group.objects.get_or_create(name='DONOR')
        group.user_set.add(self.user)
        self.donor = Donor.objects.create(
            user=self.user, bloodgroup='O+', address='Addr',
            mobile='+919385425650', aadhaar_number='111111111111',
            is_approved=False,
        )

    def test_pending_donor_gets_warning(self):
        resp = self.client.post(reverse('donorlogin'), {
            'username': 'pendingdonor', 'password': 'Str0ng!Pass99',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'pending admin approval')

    def test_approved_donor_can_login(self):
        # Approve
        self.donor.is_approved = True
        self.donor.save()
        self.user.is_active = True
        self.user.save()

        resp = self.client.post(reverse('donorlogin'), {
            'username': 'pendingdonor', 'password': 'Str0ng!Pass99',
        })
        self.assertRedirects(resp, reverse('donor-dashboard'))


class PatientLoginBlockingTests(TestCase):
    """Unapproved patients cannot login."""

    def setUp(self):
        self.user = User.objects.create_user('pendingpatient', password='Str0ng!Pass99',
                                              first_name='Pending', last_name='Patient')
        self.user.is_active = False
        self.user.save()
        group, _ = Group.objects.get_or_create(name='PATIENT')
        group.user_set.add(self.user)
        self.patient = Patient.objects.create(
            user=self.user, age=25, bloodgroup='A+', disease='None',
            doctorname='Dr. A', address='Addr', mobile='+919385425650',
            aadhaar_number='222222222222', is_approved=False,
        )

    def test_pending_patient_gets_warning(self):
        resp = self.client.post(reverse('patientlogin'), {
            'username': 'pendingpatient', 'password': 'Str0ng!Pass99',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'pending admin approval')

    def test_approved_patient_can_login(self):
        self.patient.is_approved = True
        self.patient.save()
        self.user.is_active = True
        self.user.save()

        resp = self.client.post(reverse('patientlogin'), {
            'username': 'pendingpatient', 'password': 'Str0ng!Pass99',
        })
        self.assertRedirects(resp, reverse('patient-dashboard'))


# ===========================================================================
# 9. ADMIN APPROVAL WORKFLOW — DONOR
# ===========================================================================

class AdminApproveDonorTests(TestCase):
    """Admin can view, approve, and reject pending donor registrations."""

    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'admin@test.com', 'Admin123!')
        self.client.force_login(self.admin)

        # Pending donor
        self.donor_user = User.objects.create_user('pendingd', password='Str0ng!Pass99',
                                                     first_name='Pending', last_name='D')
        self.donor_user.is_active = False
        self.donor_user.save()
        group, _ = Group.objects.get_or_create(name='DONOR')
        group.user_set.add(self.donor_user)
        self.donor = Donor.objects.create(
            user=self.donor_user, bloodgroup='A+', address='Addr',
            mobile='+919385425650', aadhaar_number='333333333333',
            is_approved=False,
        )

    # --- Pending approvals list ---
    def test_pending_approvals_page_renders(self):
        resp = self.client.get(reverse('admin-pending-approvals'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Pending')

    def test_pending_approvals_lists_donor(self):
        resp = self.client.get(reverse('admin-pending-approvals'))
        self.assertIn(self.donor, list(resp.context['pending_donors']))

    # --- Approve donor detail page ---
    def test_approve_donor_detail_renders(self):
        resp = self.client.get(reverse('admin-approve-donor', args=[self.donor.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['donor'], self.donor)

    @patch('blood.services.sms.send_welcome_sms')
    def test_approve_donor_action(self, mock_sms):
        resp = self.client.post(reverse('admin-approve-donor', args=[self.donor.pk]), {
            'action': 'approve',
        })
        self.assertRedirects(resp, reverse('admin-pending-approvals'))

        self.donor.refresh_from_db()
        self.donor_user.refresh_from_db()
        self.assertTrue(self.donor.is_approved)
        self.assertTrue(self.donor_user.is_active)
        self.assertIsNotNone(self.donor.approved_at)

    @patch('blood.services.sms.send_welcome_sms')
    def test_approve_creates_welcome_notification(self, mock_sms):
        self.client.post(reverse('admin-approve-donor', args=[self.donor.pk]), {
            'action': 'approve',
        })
        notifs = InAppNotification.objects.filter(donor=self.donor, title='Welcome to BloodBridge!')
        self.assertTrue(notifs.exists())

    @patch('blood.services.sms.send_welcome_sms')
    def test_approve_sends_welcome_sms(self, mock_sms):
        self.client.post(reverse('admin-approve-donor', args=[self.donor.pk]), {
            'action': 'approve',
        })
        mock_sms.assert_called_once_with(
            phone=self.donor.mobile,
            first_name=self.donor_user.first_name,
            role='Donor',
        )

    def test_reject_donor_action(self):
        resp = self.client.post(reverse('admin-approve-donor', args=[self.donor.pk]), {
            'action': 'reject',
            'rejection_reason': 'Invalid documents',
        })
        self.assertRedirects(resp, reverse('admin-pending-approvals'))

        self.donor.refresh_from_db()
        self.donor_user.refresh_from_db()
        self.assertFalse(self.donor.is_approved)
        self.assertFalse(self.donor_user.is_active)
        self.assertEqual(self.donor.rejection_reason, 'Invalid documents')

    def test_non_admin_redirected(self):
        """Non-superuser cannot access admin approve pages."""
        self.client.logout()
        plain_user = User.objects.create_user('plain', password='Pass1234!')
        self.client.force_login(plain_user)

        resp = self.client.get(reverse('admin-pending-approvals'))
        self.assertEqual(resp.status_code, 302)  # redirect to admin login

        resp = self.client.get(reverse('admin-approve-donor', args=[self.donor.pk]))
        self.assertEqual(resp.status_code, 302)


# ===========================================================================
# 10. ADMIN APPROVAL WORKFLOW — PATIENT
# ===========================================================================

class AdminApprovePatientTests(TestCase):
    """Admin can view, approve, and reject pending patient registrations."""

    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'admin@test.com', 'Admin123!')
        self.client.force_login(self.admin)

        self.patient_user = User.objects.create_user('pendingp', password='Str0ng!Pass99',
                                                       first_name='Pending', last_name='P')
        self.patient_user.is_active = False
        self.patient_user.save()
        group, _ = Group.objects.get_or_create(name='PATIENT')
        group.user_set.add(self.patient_user)
        self.patient = Patient.objects.create(
            user=self.patient_user, age=28, bloodgroup='B-',
            disease='Anemia', doctorname='Dr. Test',
            address='Addr', mobile='+919385425650',
            aadhaar_number='444444444444', is_approved=False,
        )

    def test_pending_approvals_lists_patient(self):
        resp = self.client.get(reverse('admin-pending-approvals'))
        self.assertIn(self.patient, list(resp.context['pending_patients']))

    def test_approve_patient_detail_renders(self):
        resp = self.client.get(reverse('admin-approve-patient', args=[self.patient.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['patient'], self.patient)

    @patch('blood.services.sms.send_welcome_sms')
    def test_approve_patient_action(self, mock_sms):
        resp = self.client.post(reverse('admin-approve-patient', args=[self.patient.pk]), {
            'action': 'approve',
        })
        self.assertRedirects(resp, reverse('admin-pending-approvals'))

        self.patient.refresh_from_db()
        self.patient_user.refresh_from_db()
        self.assertTrue(self.patient.is_approved)
        self.assertTrue(self.patient_user.is_active)
        self.assertIsNotNone(self.patient.approved_at)

    @patch('blood.services.sms.send_welcome_sms')
    def test_approve_creates_welcome_notification(self, mock_sms):
        self.client.post(reverse('admin-approve-patient', args=[self.patient.pk]), {
            'action': 'approve',
        })
        notifs = InAppNotification.objects.filter(patient=self.patient, title='Welcome to BloodBridge!')
        self.assertTrue(notifs.exists())

    @patch('blood.services.sms.send_welcome_sms')
    def test_approve_sends_welcome_sms(self, mock_sms):
        self.client.post(reverse('admin-approve-patient', args=[self.patient.pk]), {
            'action': 'approve',
        })
        mock_sms.assert_called_once_with(
            phone=self.patient.mobile,
            first_name=self.patient_user.first_name,
            role='Patient',
        )

    def test_reject_patient_action(self):
        resp = self.client.post(reverse('admin-approve-patient', args=[self.patient.pk]), {
            'action': 'reject',
            'rejection_reason': 'Prescription unclear',
        })
        self.assertRedirects(resp, reverse('admin-pending-approvals'))

        self.patient.refresh_from_db()
        self.patient_user.refresh_from_db()
        self.assertFalse(self.patient.is_approved)
        self.assertFalse(self.patient_user.is_active)
        self.assertEqual(self.patient.rejection_reason, 'Prescription unclear')

    def test_non_admin_blocked(self):
        self.client.logout()
        plain = User.objects.create_user('plain2', password='Pass1234!')
        self.client.force_login(plain)
        resp = self.client.get(reverse('admin-approve-patient', args=[self.patient.pk]))
        self.assertEqual(resp.status_code, 302)


# ===========================================================================
# 11. DOCTOR PRESCRIPTION ON BLOOD REQUEST
# ===========================================================================

class PatientRequestPrescriptionTests(TestCase, _ApprovedPatientMixin):
    """Every blood request (including emergency) requires a doctor prescription."""

    def setUp(self):
        self.user, self.patient = self._make_approved_patient()
        self.client.login(username='patient1', password='Str0ng!Pass99')
        for bg in ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-']:
            Stock.objects.get_or_create(bloodgroup=bg, defaults={'unit': 10})

    def _request_data(self, **overrides):
        base = {
            'patient_name': 'John Doe', 'patient_age': '30',
            'reason': 'Surgery requiring blood transfusion',
            'bloodgroup': 'A+', 'unit': '200', 'request_zipcode': '600001',
        }
        base.update(overrides)
        return base

    def test_request_without_prescription_fails(self):
        resp = self.client.post(reverse('make-request'), self._request_data())
        self.assertEqual(resp.status_code, 200)  # stays on form
        self.assertContains(resp, 'Doctor prescription is mandatory')
        self.assertFalse(BloodRequest.objects.filter(patient=self.patient).exists())

    @patch('blood.tasks.send_requester_confirmation_sms.delay', side_effect=Exception('no celery'))
    @patch('blood.tasks.send_urgent_alerts.delay', side_effect=Exception('no celery'))
    @patch('blood.services.sms.notify_matched_donors')
    @patch('blood.services.sms.send_requester_confirmation')
    def test_request_with_prescription_succeeds(self, mock_confirm, mock_donors, *_):
        data = self._request_data()
        data['doctor_prescription'] = _fake_pdf('rx.pdf')
        resp = self.client.post(reverse('make-request'), data)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(BloodRequest.objects.filter(patient=self.patient).exists())

    def test_urgent_request_without_prescription_fails(self):
        """Even urgent/emergency requests need a prescription."""
        data = self._request_data(is_urgent='on')
        resp = self.client.post(reverse('make-request'), data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Doctor prescription is mandatory')


# ===========================================================================
# 12. MEDICAL REPORT UPLOAD / RENEWAL VIEW
# ===========================================================================

@override_settings(GEOCODER_ALLOW_REMOTE=False)
class DonorMedicalReportViewTests(TestCase, _ApprovedDonorMixin):
    """Tests for donor medical report upload/renewal page."""

    def setUp(self):
        self.user, self.donor = self._make_approved_donor()
        self.client.login(username='donor1', password='Str0ng!Pass99')

    def test_page_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse('donor-medical-reports'))
        self.assertEqual(resp.status_code, 302)

    def test_page_renders_for_donor(self):
        resp = self.client.get(reverse('donor-medical-reports'))
        self.assertEqual(resp.status_code, 200)

    def test_upload_report_success(self):
        resp = self.client.post(reverse('donor-medical-reports'), {
            'document': _fake_pdf('new_report.pdf'),
            'notes': 'Annual checkup',
        })
        self.assertRedirects(resp, reverse('donor-medical-reports'))
        self.assertEqual(MedicalReport.objects.filter(donor=self.donor).count(), 1)

    def test_upload_without_file_fails(self):
        resp = self.client.post(reverse('donor-medical-reports'), {'notes': 'no file'})
        self.assertEqual(resp.status_code, 200)  # stays on form
        self.assertEqual(MedicalReport.objects.filter(donor=self.donor).count(), 0)

    def test_context_includes_validity_info(self):
        MedicalReport.objects.create(
            donor=self.donor, document=_fake_pdf(), document_name='r.pdf',
        )
        resp = self.client.get(reverse('donor-medical-reports'))
        self.assertTrue(resp.context['is_report_valid'])
        self.assertIsNotNone(resp.context['expiry_date'])
        self.assertIsNotNone(resp.context['days_remaining'])

    def test_non_donor_redirected(self):
        """Patient user cannot access donor medical reports page."""
        self.client.logout()
        patient_user = User.objects.create_user('patientx', password='Pass1234!')
        Group.objects.get_or_create(name='PATIENT')
        self.client.login(username='patientx', password='Pass1234!')
        resp = self.client.get(reverse('donor-medical-reports'))
        self.assertRedirects(resp, reverse('donorlogin'))


# ===========================================================================
# 13. MANAGEMENT COMMAND: send_medical_report_reminders
# ===========================================================================

class MedicalReportReminderCommandTests(TestCase, _ApprovedDonorMixin):
    """Tests for the send_medical_report_reminders management command."""

    def setUp(self):
        self.user, self.donor = self._make_approved_donor()

    def test_dry_run_no_report(self):
        """Donor without a report triggers an expired reminder in dry-run mode."""
        out = io.StringIO()
        call_command('send_medical_report_reminders', '--dry-run', stdout=out)
        output = out.getvalue()
        self.assertIn('DRY RUN', output)
        # No actual notification created
        self.assertEqual(InAppNotification.objects.filter(donor=self.donor).count(), 0)

    @patch('blood.management.commands.send_medical_report_reminders.send_sms',
           create=True, side_effect=Exception('no sms'))
    def test_expired_report_sends_reminder(self, mock_sms):
        """Donor with an expired report gets a reminder notification."""
        report = MedicalReport.objects.create(
            donor=self.donor, document=_fake_pdf(), document_name='old.pdf',
        )
        past = timezone.now() - timedelta(days=MEDICAL_REPORT_VALIDITY_DAYS + 5)
        MedicalReport.objects.filter(pk=report.pk).update(uploaded_at=past)

        out = io.StringIO()
        call_command('send_medical_report_reminders', stdout=out)
        output = out.getvalue()
        self.assertIn('expired', output.lower())

        notifs = InAppNotification.objects.filter(donor=self.donor, title='Medical Report Expired')
        self.assertTrue(notifs.exists())

    @patch('blood.management.commands.send_medical_report_reminders.send_sms',
           create=True, side_effect=Exception('no sms'))
    def test_expiring_soon_report_sends_reminder(self, mock_sms):
        """Donor with report expiring in 7 days gets an expiring-soon reminder."""
        report = MedicalReport.objects.create(
            donor=self.donor, document=_fake_pdf(), document_name='soon.pdf',
        )
        expiring_soon_date = timezone.now() - timedelta(days=MEDICAL_REPORT_VALIDITY_DAYS - 7)
        MedicalReport.objects.filter(pk=report.pk).update(uploaded_at=expiring_soon_date)

        out = io.StringIO()
        call_command('send_medical_report_reminders', '--days-before=14', stdout=out)
        output = out.getvalue()
        self.assertIn('expiring', output.lower())

        notifs = InAppNotification.objects.filter(donor=self.donor, title='Medical Report Expiring Soon')
        self.assertTrue(notifs.exists())

    def test_fresh_report_no_reminder(self):
        """Donor with a fresh report (just uploaded) gets no reminder."""
        MedicalReport.objects.create(
            donor=self.donor, document=_fake_pdf(), document_name='fresh.pdf',
        )
        out = io.StringIO()
        call_command('send_medical_report_reminders', stdout=out)
        output = out.getvalue()
        # 0 expiring, 0 expired
        self.assertIn('0 expiring', output)
        self.assertIn('0 expired', output)


# ===========================================================================
# 14. DONOR RECOMMENDER PENALTY (expired / expiring medical reports)
# ===========================================================================

class DonorRecommenderMedicalReportPenaltyTests(TestCase, _ApprovedDonorMixin):
    """donor_recommender should penalize donors with expired/expiring medical reports."""

    def setUp(self):
        self.user, self.donor = self._make_approved_donor()
        for bg in ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-']:
            Stock.objects.get_or_create(bloodgroup=bg, defaults={'unit': 10})

    def test_expired_report_flagged_as_invalid(self):
        """A donor with an expired medical report has is_medical_report_valid=False."""
        report = MedicalReport.objects.create(
            donor=self.donor, document=_fake_pdf(), document_name='old.pdf',
        )
        past = timezone.now() - timedelta(days=MEDICAL_REPORT_VALIDITY_DAYS + 5)
        MedicalReport.objects.filter(pk=report.pk).update(uploaded_at=past)

        self.assertFalse(self.donor.is_medical_report_valid)
        self.assertLess(self.donor.medical_report_days_remaining, 0)

    def test_expiring_soon_report_days_remaining(self):
        """A donor with report expiring in ~7 days has small positive days_remaining."""
        report = MedicalReport.objects.create(
            donor=self.donor, document=_fake_pdf(), document_name='soon.pdf',
        )
        days_until_expiry = 7
        backdate = timezone.now() - timedelta(days=MEDICAL_REPORT_VALIDITY_DAYS - days_until_expiry)
        MedicalReport.objects.filter(pk=report.pk).update(uploaded_at=backdate)

        remaining = self.donor.medical_report_days_remaining
        self.assertIsNotNone(remaining)
        self.assertGreater(remaining, 0)
        self.assertLessEqual(remaining, days_until_expiry + 1)

    def test_fresh_report_no_penalty_flag(self):
        """A donor with a valid report has is_medical_report_valid=True."""
        MedicalReport.objects.create(
            donor=self.donor, document=_fake_pdf(), document_name='fresh.pdf',
        )
        self.assertTrue(self.donor.is_medical_report_valid)


# ===========================================================================
# 15. URL ROUTING TESTS
# ===========================================================================

class NewURLRoutingTests(TestCase):
    """Verify all new URL patterns resolve correctly."""

    def test_donor_pending_approval_url(self):
        url = reverse('donor-pending-approval')
        self.assertEqual(url, '/donor/pending-approval/')

    def test_donor_medical_reports_url(self):
        url = reverse('donor-medical-reports')
        self.assertEqual(url, '/donor/medical-reports/')

    def test_patient_pending_approval_url(self):
        url = reverse('patient-pending-approval')
        self.assertEqual(url, '/patient/pending-approval/')

    def test_admin_pending_approvals_url(self):
        url = reverse('admin-pending-approvals')
        self.assertEqual(url, '/admin-pending-approvals/')

    def test_admin_approve_donor_url(self):
        url = reverse('admin-approve-donor', args=[1])
        self.assertEqual(url, '/admin-approve-donor/1/')

    def test_admin_approve_patient_url(self):
        url = reverse('admin-approve-patient', args=[1])
        self.assertEqual(url, '/admin-approve-patient/1/')


# ===========================================================================
# 16. END-TO-END FLOW: Signup → Admin Approve → Login
# ===========================================================================

@override_settings(GEOCODER_ALLOW_REMOTE=False)
class EndToEndDonorApprovalFlowTests(TestCase):
    """Full journey: donor signs up → account pending → admin approves → donor logins."""

    @patch('blood.services.sms.send_welcome_sms')
    @override_settings(GEOCODER_ALLOW_REMOTE=False)
    def test_full_donor_flow(self, mock_sms):
        # Step 1: Signup
        data = {
            'first_name': 'E2E', 'last_name': 'Donor',
            'username': 'e2edonor', 'password': 'Str0ng!Pass99',
            'aadhaar_number': '555555555555',
            'bloodgroup': 'O-', 'address': 'E2E Street',
            'mobile': '9385425650', 'sex': 'M',
            'document': _fake_pdf(),
        }
        resp = self.client.post(reverse('donorsignup'), data)
        self.assertEqual(resp.status_code, 302)

        user = User.objects.get(username='e2edonor')
        donor = Donor.objects.get(user=user)
        self.assertFalse(user.is_active)
        self.assertFalse(donor.is_approved)

        # Step 2: Try to login → blocked
        resp = self.client.post(reverse('donorlogin'), {
            'username': 'e2edonor', 'password': 'Str0ng!Pass99',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'pending admin approval')

        # Step 3: Admin approves
        admin_user = User.objects.create_superuser('e2eadmin', 'a@b.com', 'Admin123!')
        self.client.force_login(admin_user)
        resp = self.client.post(reverse('admin-approve-donor', args=[donor.pk]), {
            'action': 'approve',
        })
        self.assertRedirects(resp, reverse('admin-pending-approvals'))
        self.client.logout()

        # Step 4: Donor can now login
        resp = self.client.post(reverse('donorlogin'), {
            'username': 'e2edonor', 'password': 'Str0ng!Pass99',
        })
        self.assertRedirects(resp, reverse('donor-dashboard'))


class EndToEndPatientApprovalFlowTests(TestCase):
    """Full journey: patient signs up → pending → admin approves → login."""

    @patch('blood.services.sms.send_welcome_sms')
    def test_full_patient_flow(self, mock_sms):
        # Step 1: Signup
        data = {
            'first_name': 'E2EP', 'last_name': 'Patient',
            'username': 'e2epatient', 'password': 'Str0ng!Pass99',
            'aadhaar_number': '666666666666',
            'age': 30, 'bloodgroup': 'AB+', 'disease': 'Test',
            'doctorname': 'Dr. E2E', 'address': 'E2E Rd',
            'mobile': '9385425650',
            'doctor_prescription': _fake_pdf('rx.pdf'),
        }
        resp = self.client.post(reverse('patientsignup'), data)
        self.assertEqual(resp.status_code, 302)

        user = User.objects.get(username='e2epatient')
        patient = Patient.objects.get(user=user)
        self.assertFalse(user.is_active)
        self.assertFalse(patient.is_approved)

        # Step 2: Login blocked
        resp = self.client.post(reverse('patientlogin'), {
            'username': 'e2epatient', 'password': 'Str0ng!Pass99',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'pending admin approval')

        # Step 3: Admin approves
        admin_user = User.objects.create_superuser('e2eadmin2', 'a2@b.com', 'Admin123!')
        self.client.force_login(admin_user)
        resp = self.client.post(reverse('admin-approve-patient', args=[patient.pk]), {
            'action': 'approve',
        })
        self.assertRedirects(resp, reverse('admin-pending-approvals'))
        self.client.logout()

        # Step 4: Patient can login
        resp = self.client.post(reverse('patientlogin'), {
            'username': 'e2epatient', 'password': 'Str0ng!Pass99',
        })
        self.assertRedirects(resp, reverse('patient-dashboard'))


# ===========================================================================
# 17. END-TO-END REJECTION FLOW
# ===========================================================================

class EndToEndDonorRejectionFlowTests(TestCase):
    """Donor signs up → admin rejects → login remains blocked."""

    @override_settings(GEOCODER_ALLOW_REMOTE=False)
    def test_rejection_keeps_account_locked(self):
        # Signup
        data = {
            'first_name': 'Rej', 'last_name': 'Donor',
            'username': 'rejdonor', 'password': 'Str0ng!Pass99',
            'aadhaar_number': '777777777777',
            'bloodgroup': 'B+', 'address': 'Reject St',
            'mobile': '9385425650', 'sex': 'F',
            'document': _fake_pdf(),
        }
        self.client.post(reverse('donorsignup'), data)
        donor = Donor.objects.get(user__username='rejdonor')

        # Admin rejects
        admin = User.objects.create_superuser('rejadmin', 'r@b.com', 'Admin123!')
        self.client.force_login(admin)
        self.client.post(reverse('admin-approve-donor', args=[donor.pk]), {
            'action': 'reject',
            'rejection_reason': 'Blurry documents',
        })
        self.client.logout()

        donor.refresh_from_db()
        self.assertEqual(donor.rejection_reason, 'Blurry documents')
        self.assertFalse(donor.is_approved)

        # Donor still cannot login
        resp = self.client.post(reverse('donorlogin'), {
            'username': 'rejdonor', 'password': 'Str0ng!Pass99',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'pending admin approval')


# ===========================================================================
# 18. EXISTING PATIENT TESTS — updated for new mandatory fields
# ===========================================================================

class ExistingPatientLoginCompatibilityTest(TestCase):
    """Existing approved patients (backfilled) can still login."""

    def setUp(self):
        self.user = User.objects.create_user('oldpatient', password='Secret123!')
        self.user.is_active = True
        self.user.save()
        group, _ = Group.objects.get_or_create(name='PATIENT')
        group.user_set.add(self.user)
        # Simulate backfilled patient (is_approved=True, aadhaar default='')
        self.patient = Patient.objects.create(
            user=self.user, age=28, bloodgroup='A+',
            disease='None', doctorname='Dr. Legacy',
            address='Old Addr', mobile='+919385425650',
            is_approved=True, aadhaar_number='',
        )

    def test_old_patient_can_login(self):
        resp = self.client.post(reverse('patientlogin'), {
            'username': 'oldpatient', 'password': 'Secret123!',
        })
        self.assertRedirects(resp, reverse('patient-dashboard'))


class ExistingDonorLoginCompatibilityTest(TestCase, _ApprovedDonorMixin):
    """Existing approved donors (backfilled) can still login."""

    def setUp(self):
        self.user, self.donor = self._make_approved_donor(
            username='olddonor', aadhaar='',
        )

    def test_old_donor_can_login(self):
        resp = self.client.post(reverse('donorlogin'), {
            'username': 'olddonor', 'password': 'Str0ng!Pass99',
        })
        self.assertRedirects(resp, reverse('donor-dashboard'))
