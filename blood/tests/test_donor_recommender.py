from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from donor.models import Donor, MedicalReport
from blood.models import BloodRequest
from blood.services.donor_recommender import recommend_donors_for_request


def _add_valid_medical_report(donor):
    """Create a valid (non-expired) medical report for testing."""
    return MedicalReport.objects.create(
        donor=donor,
        document=SimpleUploadedFile('report.pdf', b'%PDF-1.4 fake', content_type='application/pdf'),
        document_name='report.pdf',
    )


class DonorRecommenderTests(TestCase):
    def test_recommender_filters_by_recovery(self):
        user = User.objects.create(username="d1")
        donor = Donor.objects.create(
            user=user,
            bloodgroup="O+",
            address="x",
            mobile="1",
            is_available=True,
            last_donated_at=timezone.now().date() - timedelta(days=10),
        )
        _add_valid_medical_report(donor)
        req = BloodRequest.objects.create(
            patient=None,
            request_by_donor=None,
            patient_name="p",
            patient_age=30,
            reason="r",
            bloodgroup="O+",
            unit=200,
            status="Pending",
        )

        recs = recommend_donors_for_request(req, require_eligible=True)
        self.assertEqual(len(recs), 0)

        donor.last_donated_at = timezone.now().date() - timedelta(days=120)
        donor.save(update_fields=["last_donated_at"])

        recs = recommend_donors_for_request(req, require_eligible=True)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].donor.id, donor.id)

    def test_recommender_requires_exact_bloodgroup(self):
        user = User.objects.create(username="d2")
        Donor.objects.create(
            user=user,
            bloodgroup="A+",
            address="x",
            mobile="1",
            is_available=True,
            last_donated_at=None,
        )
        req = BloodRequest.objects.create(
            patient=None,
            request_by_donor=None,
            patient_name="p",
            patient_age=30,
            reason="r",
            bloodgroup="O+",
            unit=200,
            status="Pending",
        )
        recs = recommend_donors_for_request(req, require_eligible=True)
        self.assertEqual(recs, [])

    def test_recommender_orders_available_first_when_including_ineligible(self):
        req = BloodRequest.objects.create(
            patient=None,
            request_by_donor=None,
            patient_name="p",
            patient_age=30,
            reason="r",
            bloodgroup="A+",
            unit=200,
            status="Pending",
        )

        u1 = User.objects.create(username="avail")
        d1 = Donor.objects.create(
            user=u1,
            bloodgroup="A+",
            address="x",
            mobile="1",
            is_available=True,
            last_donated_at=None,
        )

        u2 = User.objects.create(username="unavail")
        d2 = Donor.objects.create(
            user=u2,
            bloodgroup="A+",
            address="x",
            mobile="2",
            is_available=False,
            last_donated_at=None,
        )

        recs = recommend_donors_for_request(req, require_eligible=False, limit=10)
        self.assertEqual([r.donor.id for r in recs[:2]], [d1.id, d2.id])
