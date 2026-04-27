from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from blood import models as bmodels
from blood.services import sms as sms_service
from donor import models as dmodels
from patient import models as pmodels


@dataclass
class CapturedSMS:
    to: str
    message: str


class CaptureSender:
    def __init__(self) -> None:
        self.messages: List[CapturedSMS] = []

    def __call__(self, phone: str, message: str) -> Dict[str, Any]:
        self.messages.append(CapturedSMS(to=phone, message=message))
        return {"status": "success", "to": phone}


class FakeSNSClient:
    def __init__(self) -> None:
        self.publishes: List[Tuple[str, str, Dict[str, Any]]] = []

    def publish(self, *, PhoneNumber: str, Message: str, MessageAttributes: Dict[str, Any]) -> Dict[str, Any]:
        self.publishes.append((PhoneNumber, Message, MessageAttributes))
        return {"MessageId": "preview"}


class Command(BaseCommand):
    help = "Preview all SMS message formats without sending real SMS"

    def add_arguments(self, parser):
        parser.add_argument(
            "--patient",
            required=True,
            help="Patient phone number (e.g., 9385426550 or +919385426550)",
        )
        parser.add_argument(
            "--donor",
            required=True,
            help="Donor phone number (e.g., 9361046558 or +919361046558)",
        )

    def handle(self, *args, **options):
        patient_phone = str(options["patient"]).strip()
        donor_phone = str(options["donor"]).strip()

        suffix = timezone.now().strftime("%Y%m%d%H%M%S")

        created_users: List[User] = []
        created_objects: List[Any] = []

        def _create_user(username: str, first: str, last: str) -> User:
            user = User.objects.create(username=username, first_name=first, last_name=last)
            user.set_password("preview")
            user.save(update_fields=["password"])
            created_users.append(user)
            return user

        try:
            # Use synthetic groups to avoid matching existing demo donors in db.sqlite3.
            match_group = "X+"
            no_rec_group = "Y-"

            patient_user = _create_user(f"preview_patient_{suffix}", "Preview", "Patient")
            donor_user = _create_user(f"preview_donor_{suffix}", "Preview", "Donor")

            patient = pmodels.Patient.objects.create(
                user=patient_user,
                age=30,
                bloodgroup=match_group,
                disease="PreviewCase",
                doctorname="Dr Preview",
                address="Preview Address",
                mobile=patient_phone,
            )
            donor = dmodels.Donor.objects.create(
                user=donor_user,
                bloodgroup=match_group,
                address="Preview Donor Address",
                mobile=donor_phone,
                is_available=True,
                zipcode="560001",
            )
            created_objects.extend([patient, donor])

            # Urgent request (will match created donor)
            urgent_request = bmodels.BloodRequest.objects.create(
                patient=patient,
                patient_name=patient.get_name,
                patient_age=patient.age,
                reason="Preview urgent case",
                bloodgroup=match_group,
                unit=500,
                status="Pending",
                is_urgent=True,
                request_zipcode="560001",
            )
            created_objects.append(urgent_request)

            # Approved request with no recommendations
            no_match_request = bmodels.BloodRequest.objects.create(
                patient=patient,
                patient_name=patient.get_name,
                patient_age=patient.age,
                reason="Preview no-match case",
                bloodgroup=no_rec_group,
                unit=350,
                status="Approved",
                is_urgent=False,
                request_zipcode="560001",
            )
            created_objects.append(no_match_request)

            # Donation
            donation = dmodels.BloodDonate.objects.create(
                donor=donor,
                disease="Nothing",
                age=28,
                bloodgroup=donor.bloodgroup,
                unit=450,
                status="Pending",
            )
            created_objects.append(donation)

            fake_sns = FakeSNSClient()
            capture = CaptureSender()

            self.stdout.write("\n=== 1) Urgent donor broadcast (notify_matched_donors) ===")
            result = sms_service.notify_matched_donors(urgent_request, contact_number=patient_phone, sns_client=fake_sns)
            self.stdout.write(f"Result: enabled={result.enabled} attempted={result.attempted} delivered={result.delivered} reason={result.reason}\n")
            for i, (to, msg, _attrs) in enumerate(fake_sns.publishes, start=1):
                self.stdout.write(f"[{i}] TO: {to}\nMSG: {msg}\n")

            self.stdout.write("\n=== 2) Requester confirmation (send_requester_confirmation) ===")
            sms_service.send_requester_confirmation(urgent_request, patient_phone, sms_sender=capture)
            for i, item in enumerate(capture.messages, start=1):
                self.stdout.write(f"[{i}] TO: {item.to}\nMSG: {item.message}\n")
            capture.messages.clear()

            self.stdout.write("\n=== 3) Admin approves request (notify_request_approved) with top donor ===")
            urgent_request.status = "Approved"
            urgent_request.save(update_fields=["status"])
            sms_service.notify_request_approved(urgent_request, sms_sender=capture)
            for i, item in enumerate(capture.messages, start=1):
                self.stdout.write(f"[{i}] TO: {item.to}\nMSG: {item.message}\n")
            capture.messages.clear()

            self.stdout.write("\n=== 4) Admin approves request (notify_request_approved) with NO recommendations ===")
            sms_service.notify_request_approved(no_match_request, sms_sender=capture)
            for i, item in enumerate(capture.messages, start=1):
                self.stdout.write(f"[{i}] TO: {item.to}\nMSG: {item.message}\n")
            capture.messages.clear()

            self.stdout.write("\n=== 5) Admin rejects request (notify_request_rejected) ===")
            no_match_request.status = "Rejected"
            no_match_request.save(update_fields=["status"])
            sms_service.notify_request_rejected(
                no_match_request,
                reason="Insufficient stock for this blood group.",
                sms_sender=capture,
            )
            for i, item in enumerate(capture.messages, start=1):
                self.stdout.write(f"[{i}] TO: {item.to}\nMSG: {item.message}\n")
            capture.messages.clear()

            self.stdout.write("\n=== 6) Admin approves donation (notify_donation_approved) ===")
            donation.status = "Approved"
            donation.save(update_fields=["status"])
            sms_service.notify_donation_approved(donation, sms_sender=capture)
            for i, item in enumerate(capture.messages, start=1):
                self.stdout.write(f"[{i}] TO: {item.to}\nMSG: {item.message}\n")
            capture.messages.clear()

            self.stdout.write("\n=== 7) Admin rejects donation (notify_donation_rejected) ===")
            donation.status = "Rejected"
            donation.save(update_fields=["status"])
            sms_service.notify_donation_rejected(donation, reason="Donation not approved after review.", sms_sender=capture)
            for i, item in enumerate(capture.messages, start=1):
                self.stdout.write(f"[{i}] TO: {item.to}\nMSG: {item.message}\n")

            self.stdout.write("\nDone. (No real SMS were sent.)\n")

        finally:
            # Cleanup created rows so this command can be re-run safely.
            for obj in reversed(created_objects):
                try:
                    obj.delete()
                except Exception:
                    pass
            for u in reversed(created_users):
                try:
                    u.delete()
                except Exception:
                    pass
