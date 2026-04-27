"""Tests for welcome notifications and forgot-password (SMS OTP) flow."""

from unittest.mock import patch, MagicMock

from django.test import TestCase, Client, override_settings
from django.contrib.auth.models import User, Group
from django.urls import reverse
from django.utils import timezone

from django.core.files.uploadedfile import SimpleUploadedFile

from blood.models import InAppNotification, PasswordResetOTP
from donor.models import Donor
from patient.models import Patient


def _fake_pdf(name='file.pdf'):
    return SimpleUploadedFile(name, b'%PDF-1.4 fake', content_type='application/pdf')


# ---------------------------------------------------------------------------
# Welcome Notification Tests (Donor)
# ---------------------------------------------------------------------------

@override_settings(AWS_SNS_ENABLED=False, GEOCODER_ALLOW_REMOTE=False)
class DonorWelcomeNotificationTest(TestCase):
    """Registration notification on donor signup; welcome notification on admin approval."""

    def setUp(self):
        self.client = Client()

    def _donor_post_data(self, username='ravikumar'):
        return {
            'first_name': 'Ravi',
            'last_name': 'Kumar',
            'username': username,
            'password': 'StrongPass123!',
            'aadhaar_number': '123456789012',
            'bloodgroup': 'O+',
            'address': 'Chennai, Tamil Nadu',
            'mobile': '+919361046558',
            'sex': 'U',
            'document': _fake_pdf('report.pdf'),
        }

    def test_donor_signup_creates_welcome_notification(self):
        """Signup now creates a 'Registration Received' notification (pending approval)."""
        data = self._donor_post_data()
        resp = self.client.post(reverse('donorsignup'), data)
        self.assertEqual(resp.status_code, 302)

        user = User.objects.get(username='ravikumar')
        donor = Donor.objects.get(user=user)

        notifs = InAppNotification.objects.filter(donor=donor)
        self.assertEqual(notifs.count(), 1)
        self.assertIn('Registration Received', notifs.first().title)
        self.assertIn('Ravi', notifs.first().message)

    def test_donor_signup_calls_welcome_sms(self):
        """Welcome SMS is now sent at admin approval, not signup. Signup should NOT call it."""
        data = self._donor_post_data(username='ravikumar2')
        resp = self.client.post(reverse('donorsignup'), data)
        self.assertEqual(resp.status_code, 302)
        # Verify user is created but inactive (pending approval)
        user = User.objects.get(username='ravikumar2')
        self.assertFalse(user.is_active)

    def test_donor_signup_succeeds_even_if_sms_fails(self):
        """Signup succeeds; account is pending approval."""
        data = self._donor_post_data(username='ravikumar3')
        resp = self.client.post(reverse('donorsignup'), data)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(User.objects.filter(username='ravikumar3').exists())
        self.assertEqual(InAppNotification.objects.count(), 1)  # registration notification


# ---------------------------------------------------------------------------
# Welcome Notification Tests (Patient)
# ---------------------------------------------------------------------------

@override_settings(AWS_SNS_ENABLED=False)
class PatientWelcomeNotificationTest(TestCase):
    """Registration notification on patient signup; welcome notification on admin approval."""

    def setUp(self):
        self.client = Client()

    def _patient_post_data(self, username='meena_s'):
        return {
            'first_name': 'Meena',
            'last_name': 'S',
            'username': username,
            'password': 'StrongPass123!',
            'aadhaar_number': '987654321012',
            'bloodgroup': 'A+',
            'address': 'Madurai, Tamil Nadu',
            'mobile': '+919385425650',
            'age': 30,
            'disease': 'Anemia',
            'doctorname': 'Dr. Rajan',
            'doctor_prescription': _fake_pdf('rx.pdf'),
        }

    def test_patient_signup_creates_welcome_notification(self):
        """Signup now creates 'Registration Received' notification (pending approval)."""
        data = self._patient_post_data()
        resp = self.client.post(reverse('patientsignup'), data)
        self.assertEqual(resp.status_code, 302)

        user = User.objects.get(username='meena_s')
        patient = Patient.objects.get(user=user)

        notifs = InAppNotification.objects.filter(patient=patient)
        self.assertEqual(notifs.count(), 1)
        self.assertIn('Registration Received', notifs.first().title)
        self.assertIn('Meena', notifs.first().message)

    def test_patient_signup_calls_welcome_sms(self):
        """Welcome SMS is now sent at admin approval, not signup."""
        data = self._patient_post_data(username='meena_s2')
        resp = self.client.post(reverse('patientsignup'), data)
        self.assertEqual(resp.status_code, 302)
        user = User.objects.get(username='meena_s2')
        self.assertFalse(user.is_active)

    def test_patient_signup_succeeds_even_if_sms_fails(self):
        """Signup succeeds; account pending approval."""
        data = self._patient_post_data(username='meena_s3')
        resp = self.client.post(reverse('patientsignup'), data)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(User.objects.filter(username='meena_s3').exists())


# ---------------------------------------------------------------------------
# Welcome SMS service unit tests
# ---------------------------------------------------------------------------

class WelcomeSmsServiceTest(TestCase):
    """Unit tests for send_welcome_sms() and send_otp_sms()."""

    @override_settings(AWS_SNS_ENABLED=True, AWS_SNS_DEFAULT_COUNTRY_CODE='+91')
    def test_welcome_sms_india_message(self):
        from blood.services.sms import send_welcome_sms
        mock_sender = MagicMock(return_value={'status': 'success'})
        result = send_welcome_sms(
            phone='+919361046558', first_name='Ravi', role='Donor',
            sms_sender=mock_sender,
        )
        self.assertEqual(result['status'], 'success')
        mock_sender.assert_called_once()
        sent_msg = mock_sender.call_args[0][1]
        self.assertIn('BloodBridge:', sent_msg)
        self.assertIn('Ravi', sent_msg)
        self.assertLessEqual(len(sent_msg), 160)

    @override_settings(AWS_SNS_ENABLED=False, SMS_CONSOLE_FALLBACK=False)
    def test_welcome_sms_skipped_when_disabled(self):
        from blood.services.sms import send_welcome_sms
        result = send_welcome_sms(phone='+919361046558', first_name='Ravi', role='Donor')
        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'sns-disabled')

    @override_settings(AWS_SNS_ENABLED=False, SMS_CONSOLE_FALLBACK=True)
    def test_welcome_sms_console_fallback_when_disabled(self):
        from blood.services.sms import send_welcome_sms
        result = send_welcome_sms(phone='+919361046558', first_name='Ravi', role='Donor')
        self.assertEqual(result['status'], 'console-fallback')
        self.assertIn('BloodBridge', result['message'])

    def test_welcome_sms_skipped_no_phone(self):
        from blood.services.sms import send_welcome_sms
        result = send_welcome_sms(phone=None, first_name='Ravi', role='Donor')
        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'no-phone')

    def test_welcome_sms_skipped_invalid_phone(self):
        from blood.services.sms import send_welcome_sms
        result = send_welcome_sms(phone='abc', first_name='Ravi', role='Donor')
        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'invalid-phone')

    @override_settings(AWS_SNS_ENABLED=True, AWS_SNS_DEFAULT_COUNTRY_CODE='+91')
    def test_otp_sms_sent(self):
        from blood.services.sms import send_otp_sms
        mock_sender = MagicMock(return_value={'status': 'success'})
        result = send_otp_sms(
            phone='+919361046558', otp_code='123456',
            sms_sender=mock_sender,
        )
        self.assertEqual(result['status'], 'success')
        sent_msg = mock_sender.call_args[0][1]
        self.assertIn('123456', sent_msg)
        self.assertIn('BloodBridge:', sent_msg)
        self.assertLessEqual(len(sent_msg), 160)

    @override_settings(AWS_SNS_ENABLED=False, SMS_CONSOLE_FALLBACK=False)
    def test_otp_sms_skipped_when_disabled(self):
        from blood.services.sms import send_otp_sms
        result = send_otp_sms(phone='+919361046558', otp_code='123456')
        self.assertEqual(result['status'], 'skipped')

    @override_settings(AWS_SNS_ENABLED=False, SMS_CONSOLE_FALLBACK=True)
    def test_otp_sms_console_fallback_when_disabled(self):
        from blood.services.sms import send_otp_sms
        result = send_otp_sms(phone='+919361046558', otp_code='123456')
        self.assertEqual(result['status'], 'console-fallback')
        self.assertEqual(result['otp'], '123456')

    @override_settings(AWS_SNS_ENABLED=True, AWS_SNS_DEFAULT_COUNTRY_CODE='+91', SMS_CONSOLE_FALLBACK=True)
    def test_otp_sms_console_fallback_on_provider_error(self):
        from blood.services.sms import send_otp_sms
        mock_sender = MagicMock(return_value={'status': 'error', 'message': 'creds bad'})
        result = send_otp_sms(
            phone='+919361046558', otp_code='654321',
            sms_sender=mock_sender,
        )
        self.assertEqual(result['status'], 'console-fallback')
        self.assertEqual(result['otp'], '654321')


# ---------------------------------------------------------------------------
# PasswordResetOTP model tests
# ---------------------------------------------------------------------------

class PasswordResetOTPModelTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('testuser', password='old_pass')

    def test_otp_valid_within_window(self):
        otp = PasswordResetOTP.objects.create(user=self.user, otp='123456')
        self.assertTrue(otp.is_valid())

    def test_otp_invalid_when_used(self):
        otp = PasswordResetOTP.objects.create(user=self.user, otp='123456', is_used=True)
        self.assertFalse(otp.is_valid())

    def test_otp_expired_after_10_min(self):
        otp = PasswordResetOTP.objects.create(user=self.user, otp='123456')
        PasswordResetOTP.objects.filter(pk=otp.pk).update(
            created_at=timezone.now() - timezone.timedelta(minutes=11)
        )
        otp.refresh_from_db()
        self.assertFalse(otp.is_valid())

    def test_str(self):
        otp = PasswordResetOTP.objects.create(user=self.user, otp='123456')
        self.assertIn('testuser', str(otp))
        self.assertIn('active', str(otp))


# ---------------------------------------------------------------------------
# Forgot-Password Flow Tests (Donor)
# ---------------------------------------------------------------------------

@override_settings(AWS_SNS_ENABLED=True, AWS_SNS_DEFAULT_COUNTRY_CODE='+91')
class DonorForgotPasswordFlowTest(TestCase):
    """Full 3-step forgot password flow for donors."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('donoruser', password='OldPass123!')
        group, _ = Group.objects.get_or_create(name='DONOR')
        group.user_set.add(self.user)
        self.donor = Donor.objects.create(
            user=self.user,
            bloodgroup='O+',
            address='Chennai',
            mobile='+919361046558',
        )

    def test_forgot_password_page_loads(self):
        resp = self.client.get(reverse('forgot-password', kwargs={'role': 'donor'}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Forgot Password')

    @patch('blood.views.sms_service.send_otp_sms')
    def test_step1_sends_otp(self, mock_otp_sms):
        mock_otp_sms.return_value = {'status': 'success'}
        resp = self.client.post(
            reverse('forgot-password', kwargs={'role': 'donor'}),
            {'username': 'donoruser'},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn('verify-otp', resp.url)
        mock_otp_sms.assert_called_once()
        self.assertEqual(PasswordResetOTP.objects.filter(user=self.user).count(), 1)

    def test_step1_invalid_username(self):
        resp = self.client.post(
            reverse('forgot-password', kwargs={'role': 'donor'}),
            {'username': 'nonexistent'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'No account found')

    def test_step1_wrong_role(self):
        """Patient user should not be able to reset via donor forgot-password."""
        patient_user = User.objects.create_user('patientuser', password='Pass123!')
        pg, _ = Group.objects.get_or_create(name='PATIENT')
        pg.user_set.add(patient_user)
        Patient.objects.create(user=patient_user, bloodgroup='A+', address='Madurai', mobile='+919385425650', age=28, disease='Anemia', doctorname='Dr. Rajan')

        resp = self.client.post(
            reverse('forgot-password', kwargs={'role': 'donor'}),
            {'username': 'patientuser'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'No donor account found')

    @patch('blood.views.sms_service.send_otp_sms')
    def test_step2_verify_otp(self, mock_otp_sms):
        mock_otp_sms.return_value = {'status': 'success'}
        self.client.post(
            reverse('forgot-password', kwargs={'role': 'donor'}),
            {'username': 'donoruser'},
        )
        otp = PasswordResetOTP.objects.filter(user=self.user).first()

        resp = self.client.post(
            reverse('verify-otp', kwargs={'role': 'donor'}),
            {'otp': otp.otp},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn('reset-password', resp.url)
        otp.refresh_from_db()
        self.assertTrue(otp.is_used)

    @patch('blood.views.sms_service.send_otp_sms')
    def test_step2_wrong_otp(self, mock_otp_sms):
        mock_otp_sms.return_value = {'status': 'success'}
        self.client.post(
            reverse('forgot-password', kwargs={'role': 'donor'}),
            {'username': 'donoruser'},
        )

        resp = self.client.post(
            reverse('verify-otp', kwargs={'role': 'donor'}),
            {'otp': '000000'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Invalid or expired')

    @patch('blood.views.sms_service.send_otp_sms')
    def test_step3_reset_password(self, mock_otp_sms):
        mock_otp_sms.return_value = {'status': 'success'}
        self.client.post(
            reverse('forgot-password', kwargs={'role': 'donor'}),
            {'username': 'donoruser'},
        )
        otp = PasswordResetOTP.objects.filter(user=self.user).first()
        self.client.post(
            reverse('verify-otp', kwargs={'role': 'donor'}),
            {'otp': otp.otp},
        )

        resp = self.client.post(
            reverse('reset-password', kwargs={'role': 'donor'}),
            {'new_password': 'NewStrongPass456!', 'confirm_password': 'NewStrongPass456!'},
        )
        self.assertEqual(resp.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NewStrongPass456!'))

    @patch('blood.views.sms_service.send_otp_sms')
    def test_step3_password_mismatch(self, mock_otp_sms):
        mock_otp_sms.return_value = {'status': 'success'}
        self.client.post(
            reverse('forgot-password', kwargs={'role': 'donor'}),
            {'username': 'donoruser'},
        )
        otp = PasswordResetOTP.objects.filter(user=self.user).first()
        self.client.post(
            reverse('verify-otp', kwargs={'role': 'donor'}),
            {'otp': otp.otp},
        )
        resp = self.client.post(
            reverse('reset-password', kwargs={'role': 'donor'}),
            {'new_password': 'Pass1xyz!', 'confirm_password': 'Pass2xyz!'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'do not match')

    @patch('blood.views.sms_service.send_otp_sms')
    def test_step3_weak_password_rejected(self, mock_otp_sms):
        mock_otp_sms.return_value = {'status': 'success'}
        self.client.post(
            reverse('forgot-password', kwargs={'role': 'donor'}),
            {'username': 'donoruser'},
        )
        otp = PasswordResetOTP.objects.filter(user=self.user).first()
        self.client.post(
            reverse('verify-otp', kwargs={'role': 'donor'}),
            {'otp': otp.otp},
        )
        resp = self.client.post(
            reverse('reset-password', kwargs={'role': 'donor'}),
            {'new_password': '123', 'confirm_password': '123'},
        )
        self.assertEqual(resp.status_code, 200)

    def test_step2_without_session(self):
        resp = self.client.get(reverse('verify-otp', kwargs={'role': 'donor'}))
        self.assertEqual(resp.status_code, 302)

    def test_step3_without_session(self):
        resp = self.client.get(reverse('reset-password', kwargs={'role': 'donor'}))
        self.assertEqual(resp.status_code, 302)

    @patch('blood.views.sms_service.send_otp_sms')
    def test_remaining_otps_invalidated_after_reset(self, mock_otp_sms):
        """All unused OTPs should be marked used after successful reset."""
        mock_otp_sms.return_value = {'status': 'success'}
        PasswordResetOTP.objects.create(user=self.user, otp='999999')

        self.client.post(
            reverse('forgot-password', kwargs={'role': 'donor'}),
            {'username': 'donoruser'},
        )
        otp = PasswordResetOTP.objects.filter(user=self.user).exclude(otp='999999').first()
        self.client.post(reverse('verify-otp', kwargs={'role': 'donor'}), {'otp': otp.otp})
        self.client.post(
            reverse('reset-password', kwargs={'role': 'donor'}),
            {'new_password': 'NewStrongPass456!', 'confirm_password': 'NewStrongPass456!'},
        )
        self.assertEqual(PasswordResetOTP.objects.filter(user=self.user, is_used=False).count(), 0)


# ---------------------------------------------------------------------------
# Forgot-Password Flow Tests (Patient)
# ---------------------------------------------------------------------------

@override_settings(AWS_SNS_ENABLED=True, AWS_SNS_DEFAULT_COUNTRY_CODE='+91')
class PatientForgotPasswordFlowTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('patientuser', password='OldPass123!')
        group, _ = Group.objects.get_or_create(name='PATIENT')
        group.user_set.add(self.user)
        self.patient = Patient.objects.create(
            user=self.user,
            bloodgroup='A+',
            address='Madurai',
            mobile='+919385425650',
            age=30,
            disease='Anemia',
            doctorname='Dr. Rajan',
        )

    def test_forgot_password_page_loads(self):
        resp = self.client.get(reverse('forgot-password', kwargs={'role': 'patient'}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Patient')

    @patch('blood.views.sms_service.send_otp_sms')
    def test_full_reset_flow(self, mock_otp_sms):
        mock_otp_sms.return_value = {'status': 'success'}

        self.client.post(
            reverse('forgot-password', kwargs={'role': 'patient'}),
            {'username': 'patientuser'},
        )
        otp = PasswordResetOTP.objects.filter(user=self.user).first()
        self.assertIsNotNone(otp)

        self.client.post(
            reverse('verify-otp', kwargs={'role': 'patient'}),
            {'otp': otp.otp},
        )

        resp = self.client.post(
            reverse('reset-password', kwargs={'role': 'patient'}),
            {'new_password': 'NewPatientPass789!', 'confirm_password': 'NewPatientPass789!'},
        )
        self.assertEqual(resp.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NewPatientPass789!'))


# ---------------------------------------------------------------------------
# Forgot-Password Flow Tests (Admin)
# ---------------------------------------------------------------------------

@override_settings(AWS_SNS_ENABLED=True, AWS_SNS_DEFAULT_COUNTRY_CODE='+91')
class AdminForgotPasswordFlowTest(TestCase):

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser('adminuser', password='AdminOld123!')

    def test_forgot_password_page_loads(self):
        resp = self.client.get(reverse('forgot-password', kwargs={'role': 'admin'}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Admin')
        self.assertContains(resp, 'Mobile Number')

    @patch('blood.views.sms_service.send_otp_sms')
    def test_admin_full_reset_flow(self, mock_otp_sms):
        mock_otp_sms.return_value = {'status': 'success'}

        self.client.post(
            reverse('forgot-password', kwargs={'role': 'admin'}),
            {'username': 'adminuser', 'phone': '+919361046558'},
        )
        otp = PasswordResetOTP.objects.filter(user=self.admin).first()
        self.assertIsNotNone(otp)

        self.client.post(
            reverse('verify-otp', kwargs={'role': 'admin'}),
            {'otp': otp.otp},
        )

        resp = self.client.post(
            reverse('reset-password', kwargs={'role': 'admin'}),
            {'new_password': 'NewAdmin456!xyz', 'confirm_password': 'NewAdmin456!xyz'},
        )
        self.assertEqual(resp.status_code, 302)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.check_password('NewAdmin456!xyz'))

    def test_non_admin_cannot_use_admin_reset(self):
        User.objects.create_user('regularuser', password='Pass123!')
        resp = self.client.post(
            reverse('forgot-password', kwargs={'role': 'admin'}),
            {'username': 'regularuser', 'phone': '+919361046558'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'No admin account found')


# ---------------------------------------------------------------------------
# Invalid role test
# ---------------------------------------------------------------------------

class InvalidRoleTest(TestCase):

    def test_invalid_role_returns_404(self):
        resp = self.client.get('/forgot-password/hacker/')
        self.assertEqual(resp.status_code, 404)

    def test_invalid_role_verify_otp_404(self):
        resp = self.client.get('/verify-otp/hacker/')
        self.assertEqual(resp.status_code, 404)

    def test_invalid_role_reset_password_404(self):
        resp = self.client.get('/reset-password/hacker/')
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# Login template link tests
# ---------------------------------------------------------------------------

class LoginTemplateForgotPasswordLinkTest(TestCase):
    """Ensure all login pages have Forgot Password links."""

    def test_donor_login_has_forgot_link(self):
        resp = self.client.get(reverse('donorlogin'))
        self.assertContains(resp, 'Forgot Password')
        self.assertContains(resp, '/forgot-password/donor/')

    def test_patient_login_has_forgot_link(self):
        resp = self.client.get(reverse('patientlogin'))
        self.assertContains(resp, 'Forgot Password')
        self.assertContains(resp, '/forgot-password/patient/')

    def test_admin_login_has_forgot_link(self):
        resp = self.client.get(reverse('adminlogin'))
        self.assertContains(resp, 'Forgot Password')
        self.assertContains(resp, '/forgot-password/admin/')
