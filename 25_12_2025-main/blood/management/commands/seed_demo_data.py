import random
from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from faker import Faker

from blood import models as blood_models
from donor import models as donor_models
from patient import models as patient_models

BLOOD_GROUPS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
DONATION_UNITS = [200, 250, 300, 350, 400, 450, 500]
REQUEST_UNITS = [200, 250, 300, 350, 400, 450]
DONATION_STATUSES = ["Pending", "Approved", "Rejected"]
REQUEST_STATUSES = ["Pending", "Approved", "Rejected"]
DEFAULT_PASSWORD = "DemoPass123!"


class Command(BaseCommand):
    help = "Generate a realistic demo dataset with donors, patients, stock, donations, and request history"

    def add_arguments(self, parser):
        parser.add_argument("--donors", type=int, help="Number of donors to create (default random between 75-100)")
        parser.add_argument("--patients", type=int, help="Number of patients to create (default random between 75-100)")
        parser.add_argument("--seed", type=int, help="Random seed for deterministic runs")
        parser.add_argument("--purge", action="store_true", help="Delete existing donors/patients/requests/donations before seeding")
        parser.add_argument(
            "--ratio-unavailable",
            type=float,
            default=0.18,
            help="Fraction of donors initially marked unavailable (0.0-0.9). Default: 0.18",
        )

    def handle(self, *args, **options):
        faker = Faker()
        if options.get("seed") is not None:
            Faker.seed(options["seed"])
            random.seed(options["seed"])

        donor_target = options.get("donors") or random.randint(75, 100)
        patient_target = options.get("patients") or random.randint(75, 100)
        ratio_unavailable = float(options.get("ratio_unavailable") or 0.18)
        ratio_unavailable = max(0.0, min(0.9, ratio_unavailable))

        if options.get("purge"):
            self._purge_existing()

        donor_group = self._ensure_group("DONOR")
        patient_group = self._ensure_group("PATIENT")

        stock_cache = self._initialize_stock()

        with transaction.atomic():
            donors = self._create_donors(donor_target, donor_group, faker, ratio_unavailable=ratio_unavailable)
            patients = self._create_patients(patient_target, patient_group, faker)
            donation_count = self._create_donations(donors, stock_cache, faker)
            request_count = self._create_requests(patients, donors, stock_cache, faker)

        summary = (
            f"Seed complete: {len(donors)} donors, {len(patients)} patients, "
            f"{donation_count} donations, {request_count} blood requests."
        )
        self.stdout.write(self.style.SUCCESS(summary))
        self.stdout.write(
            self.style.SUCCESS(
                "Default password for generated accounts: '" + DEFAULT_PASSWORD + "'"
            )
        )

    # ------------------------------------------------------------------
    def _ensure_group(self, name):
        group, _ = Group.objects.get_or_create(name=name)
        return group

    def _purge_existing(self):
        self.stdout.write("Purging existing donor/patient/demo data…")
        blood_models.BloodRequest.objects.all().delete()
        donor_models.BloodDonate.objects.all().delete()

        donor_user_ids = list(donor_models.Donor.objects.values_list("user_id", flat=True))
        patient_user_ids = list(patient_models.Patient.objects.values_list("user_id", flat=True))

        # Deleting the user cascades to donor/patient profiles
        User.objects.filter(id__in=donor_user_ids).delete()
        User.objects.filter(id__in=patient_user_ids).delete()
        self.stdout.write(self.style.WARNING("Existing demo records removed."))

    def _initialize_stock(self):
        stock_cache = {}
        for group in BLOOD_GROUPS:
            stock, _ = blood_models.Stock.objects.get_or_create(bloodgroup=group)
            stock.unit = random.randint(800, 1500)
            stock.save(update_fields=["unit"])
            stock_cache[group] = stock
        return stock_cache

    def _random_username(self, prefix):
        suffix = random.randint(1000, 999999)
        username = f"{prefix}{suffix}"
        while User.objects.filter(username=username).exists():
            suffix = random.randint(1000, 999999)
            username = f"{prefix}{suffix}"
        return username

    def _create_user(self, prefix, group, faker):
        first_name = faker.first_name()
        last_name = faker.last_name()
        username = self._random_username(prefix)
        email = f"{username}@demo.local"
        user = User.objects.create_user(
            username=username,
            first_name=first_name,
            last_name=last_name,
            email=email,
            password=DEFAULT_PASSWORD,
        )
        group.user_set.add(user)
        return user

    def _create_donors(self, target, donor_group, faker, *, ratio_unavailable: float = 0.18):
        donors = []
        for _ in range(target):
            user = self._create_user("donor_", donor_group, faker)
            is_available = random.random() >= float(ratio_unavailable)
            donor = donor_models.Donor.objects.create(
                user=user,
                bloodgroup=random.choice(BLOOD_GROUPS),
                address=faker.street_address(),
                mobile=faker.msisdn()[:12],
                is_available=is_available,
                availability_updated_at=(
                    timezone.now() - timedelta(days=random.randint(0, 30), hours=random.randint(0, 23), minutes=random.randint(0, 59))
                    if (not is_available or random.random() < 0.20)
                    else None
                ),
            )
            donors.append(donor)
        return donors

    def _create_patients(self, target, patient_group, faker):
        patients = []
        for _ in range(target):
            user = self._create_user("patient_", patient_group, faker)
            patient = patient_models.Patient.objects.create(
                user=user,
                age=random.randint(1, 90),
                bloodgroup=random.choice(BLOOD_GROUPS),
                disease=random.choice([
                    "Thalassemia", "Surgery", "Accident", "Cancer Therapy", "Anemia",
                ]),
                doctorname=faker.name(),
                address=faker.street_address(),
                mobile=faker.msisdn()[:12],
            )
            patients.append(patient)
        return patients

    def _update_stock(self, stock_cache, bloodgroup, delta):
        stock = stock_cache[bloodgroup]
        stock.unit = max(stock.unit + delta, 0)
        stock.save(update_fields=["unit"])

    def _random_past_date(self):
        days_ago = random.randint(0, 200)
        return timezone.now().date() - timedelta(days=days_ago)

    def _create_donations(self, donors, stock_cache, faker):
        donation_total = 0
        for donor in donors:
            donation_events = random.randint(0, 3)
            for _ in range(donation_events):
                status = random.choices(
                    DONATION_STATUSES, weights=[2, 6, 1], k=1
                )[0]
                units = random.choice(DONATION_UNITS)
                donation = donor_models.BloodDonate.objects.create(
                    donor=donor,
                    disease=random.choice([
                        "Nothing", "Cold", "Recovered Covid", "Seasonal Allergy"
                    ]),
                    age=random.randint(18, 60),
                    bloodgroup=donor.bloodgroup,
                    unit=units,
                    status=status,
                )
                donation_total += 1
                donation_date = self._random_past_date()
                donor_models.BloodDonate.objects.filter(pk=donation.pk).update(date=donation_date)

                if status == "Approved":
                    self._update_stock(stock_cache, donor.bloodgroup, units)
        return donation_total

    def _create_requests(self, patients, donors, stock_cache, faker):
        request_total = 0
        # Patient-driven requests
        for patient in patients:
            for _ in range(random.randint(0, 2)):
                request_total += self._create_request_record(
                    patient=patient,
                    request_by_donor=None,
                    fallback_name=patient.get_name,
                    age_value=patient.age,
                    bloodgroup=patient.bloodgroup,
                    stock_cache=stock_cache,
                    faker=faker,
                )
        # Donor self-serve requests
        donor_pool = random.sample(donors, k=min(len(donors), random.randint(10, 25)))
        for donor in donor_pool:
            request_total += self._create_request_record(
                patient=None,
                request_by_donor=donor,
                fallback_name=donor.get_name,
                age_value=random.randint(18, 60),
                bloodgroup=donor.bloodgroup,
                stock_cache=stock_cache,
                faker=faker,
            )
        # Quick anonymous requests
        for _ in range(random.randint(10, 20)):
            request_total += self._create_request_record(
                patient=None,
                request_by_donor=None,
                fallback_name=faker.name(),
                age_value=random.randint(1, 90),
                bloodgroup=random.choice(BLOOD_GROUPS),
                stock_cache=stock_cache,
                faker=faker,
            )
        return request_total

    def _create_request_record(
        self,
        *,
        patient,
        request_by_donor,
        fallback_name,
        age_value,
        bloodgroup,
        stock_cache,
        faker,
    ):
        status = random.choices(REQUEST_STATUSES, weights=[3, 5, 2], k=1)[0]
        units = random.choice(REQUEST_UNITS)

        request = blood_models.BloodRequest.objects.create(
            patient=patient,
            request_by_donor=request_by_donor,
            patient_name=fallback_name,
            patient_age=age_value,
            reason=faker.sentence(nb_words=12),
            bloodgroup=bloodgroup,
            unit=units,
            status=status,
        )
        request_date = faker.date_between(start_date="-200d", end_date="today")
        blood_models.BloodRequest.objects.filter(pk=request.pk).update(date=request_date)

        if status == "Approved":
            stock = stock_cache[bloodgroup]
            if stock.unit >= units:
                self._update_stock(stock_cache, bloodgroup, -units)
            else:
                # Not enough stock, mark as rejected
                blood_models.BloodRequest.objects.filter(pk=request.pk).update(status="Rejected")
        return 1