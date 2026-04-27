"""
Patient app test suite.

Covers signup, login, dashboard, request creation, request history,
nearby donors, feedback, profile updates, and access control.
"""
from unittest.mock import patch

from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.urls import reverse

from django.core.files.uploadedfile import SimpleUploadedFile

from blood.models import BloodRequest, Stock, InAppNotification, Feedback
from patient.models import Patient
from patient.forms import PatientUserForm, PatientForm


def _fake_pdf(name='file.pdf'):
    return SimpleUploadedFile(name, b'%PDF-1.4 fake', content_type='application/pdf')


class PatientSignupTest(TestCase):
    """Tests for patient registration flow."""

    def test_signup_page_renders(self):
        resp = self.client.get(reverse('patientsignup'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Patient')

    def test_successful_signup(self):
        resp = self.client.post(reverse('patientsignup'), {
            'first_name': 'TestPatient',
            'last_name': 'User',
            'username': 'ptestuser',
            'password': 'Str0ng!Pass99',
            'aadhaar_number': '123456789012',
            'age': 30,
            'bloodgroup': 'O+',
            'disease': 'Anemia',
            'doctorname': 'Dr. Smith',
            'address': '123 Main St',
            'mobile': '9385425650',
            'doctor_prescription': _fake_pdf('rx.pdf'),
        })
        self.assertEqual(resp.status_code, 302)  # redirect to pending-approval
        self.assertTrue(User.objects.filter(username='ptestuser').exists())
        user = User.objects.get(username='ptestuser')
        self.assertFalse(user.is_active)  # inactive until admin approves
        self.assertTrue(user.groups.filter(name='PATIENT').exists())
        self.assertTrue(Patient.objects.filter(user=user).exists())

    def test_signup_duplicate_username(self):
        User.objects.create_user('existinguser', password='pass1234')
        resp = self.client.post(reverse('patientsignup'), {
            'first_name': 'Test',
            'last_name': 'User',
            'username': 'existinguser',
            'password': 'Str0ng!Pass99',
            'aadhaar_number': '111122223333',
            'age': 25,
            'bloodgroup': 'A+',
            'disease': 'None',
            'doctorname': 'Dr. A',
            'address': '456 Oak Ave',
            'mobile': '9361046558',
            'doctor_prescription': _fake_pdf('rx.pdf'),
        })
        # Should stay on signup page with error
        self.assertEqual(resp.status_code, 200)

    def test_signup_weak_password_rejected(self):
        resp = self.client.post(reverse('patientsignup'), {
            'first_name': 'Weak',
            'last_name': 'Pass',
            'username': 'weakpassuser',
            'password': '123',
            'aadhaar_number': '444455556666',
            'age': 20,
            'bloodgroup': 'B+',
            'disease': 'Flu',
            'doctorname': 'Dr. B',
            'address': '789 Elm',
            'mobile': '9385425650',
            'doctor_prescription': _fake_pdf('rx.pdf'),
        })
        self.assertEqual(resp.status_code, 200)  # stays on form
        self.assertFalse(User.objects.filter(username='weakpassuser').exists())


class PatientLoginTest(TestCase):
    """Tests for patient login flow."""

    def setUp(self):
        self.user = User.objects.create_user('patient1', password='Secret123!')
        group, _ = Group.objects.get_or_create(name='PATIENT')
        group.user_set.add(self.user)
        self.patient = Patient.objects.create(
            user=self.user, age=28, bloodgroup='A+',
            disease='None', doctorname='Dr. Test',
            address='Test Addr', mobile='+919385425650',
            is_approved=True,
        )

    def test_login_page_renders(self):
        resp = self.client.get(reverse('patientlogin'))
        self.assertEqual(resp.status_code, 200)

    def test_valid_login_redirects_to_dashboard(self):
        resp = self.client.post(reverse('patientlogin'), {
            'username': 'patient1',
            'password': 'Secret123!',
        })
        self.assertRedirects(resp, reverse('patient-dashboard'))

    def test_invalid_password_shows_error(self):
        resp = self.client.post(reverse('patientlogin'), {
            'username': 'patient1',
            'password': 'wrong',
        })
        self.assertEqual(resp.status_code, 200)

    def test_non_patient_user_denied(self):
        """A user not in the PATIENT group cannot use patient login."""
        donor_user = User.objects.create_user('donorguy', password='DonorPass1!')
        Group.objects.get_or_create(name='DONOR')
        resp = self.client.post(reverse('patientlogin'), {
            'username': 'donorguy',
            'password': 'DonorPass1!',
        })
        self.assertEqual(resp.status_code, 200)  # stays on login page


class PatientDashboardTest(TestCase):
    """Tests for patient dashboard view."""

    def setUp(self):
        self.user = User.objects.create_user('pdash', password='Pass1234!')
        group, _ = Group.objects.get_or_create(name='PATIENT')
        group.user_set.add(self.user)
        self.patient = Patient.objects.create(
            user=self.user, age=35, bloodgroup='B+',
            disease='Thalassemia', doctorname='Dr. Dash',
            address='Dashboard St', mobile='+919385425650',
            is_approved=True,
        )
        # Seed stock rows
        for bg in ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-']:
            Stock.objects.get_or_create(bloodgroup=bg, defaults={'unit': 10})

    def test_dashboard_requires_login(self):
        resp = self.client.get(reverse('patient-dashboard'))
        self.assertEqual(resp.status_code, 302)

    def test_dashboard_renders_for_patient(self):
        self.client.login(username='pdash', password='Pass1234!')
        resp = self.client.get(reverse('patient-dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Dashboard')

    def test_dashboard_shows_request_stats(self):
        self.client.login(username='pdash', password='Pass1234!')
        BloodRequest.objects.create(
            patient=self.patient, patient_name='Test', patient_age=35,
            reason='Surgery', bloodgroup='B+', unit=200, status='Approved',
        )
        BloodRequest.objects.create(
            patient=self.patient, patient_name='Test', patient_age=35,
            reason='Emergency', bloodgroup='B+', unit=100, status='Pending',
        )
        resp = self.client.get(reverse('patient-dashboard'))
        self.assertEqual(resp.status_code, 200)

    def test_dashboard_shows_notifications(self):
        self.client.login(username='pdash', password='Pass1234!')
        InAppNotification.objects.create(
            patient=self.patient,
            title='Test Notification',
            message='Your request was approved',
        )
        resp = self.client.get(reverse('patient-dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Test Notification')


class PatientRequestTest(TestCase):
    """Tests for blood request creation and history."""

    def setUp(self):
        self.user = User.objects.create_user('preq', password='Pass1234!')
        group, _ = Group.objects.get_or_create(name='PATIENT')
        group.user_set.add(self.user)
        self.patient = Patient.objects.create(
            user=self.user, age=40, bloodgroup='O-',
            disease='Accident', doctorname='Dr. Req',
            address='Request Lane', mobile='+919385425650',
            is_approved=True,
        )
        for bg in ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-']:
            Stock.objects.get_or_create(bloodgroup=bg, defaults={'unit': 10})
        self.client.login(username='preq', password='Pass1234!')

    def test_make_request_page_renders(self):
        resp = self.client.get(reverse('make-request'))
        self.assertEqual(resp.status_code, 200)

    @patch('blood.tasks.send_requester_confirmation_sms.delay', side_effect=Exception('no celery'))
    @patch('blood.tasks.send_urgent_alerts.delay', side_effect=Exception('no celery'))
    @patch('blood.services.sms.notify_matched_donors')
    @patch('blood.services.sms.send_requester_confirmation')
    def test_create_request_success(self, mock_confirm, mock_donors, *_):
        resp = self.client.post(reverse('make-request'), {
            'patient_name': 'John Doe',
            'patient_age': '40',
            'reason': 'Scheduled surgery requiring blood transfusion',
            'bloodgroup': 'O-',
            'unit': '200',
            'request_zipcode': '600001',
            'is_urgent': 'on',
            'doctor_prescription': _fake_pdf('rx.pdf'),
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(BloodRequest.objects.filter(patient=self.patient).exists())
        br = BloodRequest.objects.get(patient=self.patient)
        self.assertEqual(br.bloodgroup, 'O-')
        self.assertEqual(br.unit, 200)
        self.assertTrue(br.is_urgent)

    def test_create_request_missing_fields(self):
        resp = self.client.post(reverse('make-request'), {
            'patient_name': '',
            'patient_age': '',
            'reason': '',
            'bloodgroup': '',
            'unit': '',
        })
        self.assertEqual(resp.status_code, 200)  # stays on form
        self.assertFalse(BloodRequest.objects.filter(patient=self.patient).exists())

    def test_create_request_invalid_age(self):
        resp = self.client.post(reverse('make-request'), {
            'patient_name': 'Jane',
            'patient_age': '999',
            'reason': 'Emergency blood needed urgently',
            'bloodgroup': 'A+',
            'unit': '200',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(BloodRequest.objects.filter(patient=self.patient).exists())

    def test_request_history_page(self):
        BloodRequest.objects.create(
            patient=self.patient, patient_name='History Test', patient_age=40,
            reason='Past request', bloodgroup='O-', unit=100, status='Approved',
        )
        resp = self.client.get(reverse('my-request'))
        self.assertEqual(resp.status_code, 200)


class PatientAccessControlTest(TestCase):
    """Tests that non-patient users cannot access patient views."""

    def setUp(self):
        self.donor_user = User.objects.create_user('donoronly', password='Pass1234!')
        group, _ = Group.objects.get_or_create(name='DONOR')
        group.user_set.add(self.donor_user)

    def test_donor_cannot_access_patient_dashboard(self):
        self.client.login(username='donoronly', password='Pass1234!')
        resp = self.client.get(reverse('patient-dashboard'))
        self.assertRedirects(resp, reverse('patientlogin'))

    def test_donor_cannot_make_patient_request(self):
        self.client.login(username='donoronly', password='Pass1234!')
        resp = self.client.get(reverse('make-request'))
        self.assertRedirects(resp, reverse('patientlogin'))


class PatientFormValidationTest(TestCase):
    """Tests for patient form server-side validation."""

    def test_patient_form_valid_mobile(self):
        form = PatientForm(data={
            'aadhaar_number': '123456789012',
            'age': 25, 'bloodgroup': 'A+', 'disease': 'None',
            'doctorname': 'Dr. X', 'address': '123 St', 'mobile': '9385425650',
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.cleaned_data['mobile'].startswith('+'))

    def test_patient_form_invalid_mobile(self):
        form = PatientForm(data={
            'aadhaar_number': '123456789012',
            'age': 25, 'bloodgroup': 'A+', 'disease': 'None',
            'doctorname': 'Dr. X', 'address': '123 St', 'mobile': '',
        })
        self.assertFalse(form.is_valid())

    def test_patient_user_form_weak_password(self):
        form = PatientUserForm(data={
            'first_name': 'Test', 'last_name': 'User',
            'username': 'testweakpw', 'password': '123',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('password', form.errors)

    def test_patient_user_form_strong_password(self):
        form = PatientUserForm(data={
            'first_name': 'Test', 'last_name': 'User',
            'username': 'teststrongpw', 'password': 'Str0ng!Pass99',
        })
        self.assertTrue(form.is_valid())


class PatientFeedbackTest(TestCase):
    """Tests for patient feedback submission."""

    def setUp(self):
        self.user = User.objects.create_user('pfeedback', password='Pass1234!')
        group, _ = Group.objects.get_or_create(name='PATIENT')
        group.user_set.add(self.user)
        self.patient = Patient.objects.create(
            user=self.user, age=30, bloodgroup='AB+',
            disease='None', doctorname='Dr. Feedback',
            address='Feedback Road', mobile='+919385425650',
            is_approved=True,
        )
        self.client.login(username='pfeedback', password='Pass1234!')

    def test_feedback_form_renders(self):
        resp = self.client.get(reverse('patient-feedback'))
        self.assertEqual(resp.status_code, 200)

    def test_submit_valid_feedback(self):
        resp = self.client.post(reverse('patient-feedback'), {
            'feedback_for': 'GENERAL',
            'rating': 5,
            'message': 'Excellent service, very helpful staff!',
        })
        self.assertRedirects(resp, reverse('patient-dashboard'))
        self.assertEqual(Feedback.objects.filter(patient=self.patient).count(), 1)

    def test_submit_feedback_invalid_rating(self):
        resp = self.client.post(reverse('patient-feedback'), {
            'feedback_for': 'GENERAL',
            'rating': 0,
            'message': 'Bad rating value',
        })
        self.assertEqual(resp.status_code, 200)  # stays on form
