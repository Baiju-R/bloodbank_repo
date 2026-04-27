from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import List, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand

from blood.services import sms as sms_service
from blood.utils.phone import normalize_phone_number
from blood.utils.sms_sender import send_sms


def _mask_phone(phone: str) -> str:
    phone = phone or ""
    if len(phone) <= 4:
        return "****"
    return f"{'*' * (len(phone) - 4)}{phone[-4:]}"


@dataclass
class _DummyDonor:
    get_name: str
    bloodgroup: str
    mobile: str
    address: str
    is_available: bool = True


@dataclass
class _DummyRecommendation:
    donor: _DummyDonor
    score: float = 92.7


class Command(BaseCommand):
    help = "Send (or preview) sample SMS messages for all BloodBridge scenarios via AWS SNS."

    def add_arguments(self, parser):
        parser.add_argument(
            "to",
            type=str,
            help="Destination phone number (e.g., 9361046558 or +919361046558)",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually send the SMS messages (default is preview only)",
        )
        parser.add_argument(
            "--max",
            type=int,
            default=0,
            help="Limit number of messages to send/preview (0 = all)",
        )

    def handle(self, *args, **options):
        raw_to = (options["to"] or "").strip()
        normalized_to = normalize_phone_number(raw_to)
        if not normalized_to:
            self.stderr.write(self.style.ERROR("Invalid phone number."))
            return

        apply_changes = bool(options.get("apply"))
        max_count = int(options.get("max") or 0)

        if not getattr(settings, "AWS_SNS_ENABLED", False):
            self.stdout.write(self.style.WARNING("AWS_SNS_ENABLED is False; sending will be skipped."))

        # Build representative dummy objects for message templates.
        dummy_request = SimpleNamespace(
            id=101,
            patient_name="Ravi Kumar",
            patient_age=32,
            bloodgroup="O+",
            unit=350,
            reason="Emergency surgery - need blood",
            request_zipcode="560001",
            is_urgent=True,
            patient=None,
            request_by_donor=None,
        )

        dummy_donor = _DummyDonor(
            get_name="Ananya Sharma",
            bloodgroup="O+",
            mobile=normalized_to,
            address="MG Road, Bengaluru",
            is_available=True,
        )
        dummy_rec = _DummyRecommendation(donor=dummy_donor, score=94.2)

        cases: List[Tuple[str, str]] = []

        cases.append((
            "Connectivity Test",
            "Hello from BloodBridge! SMS working successfully.",
        ))

        cases.append((
            "Requester Confirmation",
            sms_service._build_requester_confirmation_message(dummy_request),
        ))

        cases.append((
            "Urgent Donor Alert",
            sms_service._build_message(dummy_request, normalized_to),
        ))

        cases.append((
            "Request Approved (Patient)",
            sms_service._build_patient_approved_message(dummy_request, dummy_rec),
        ))

        cases.append((
            "Request Approved (Donor)",
            sms_service._build_donor_approved_message(dummy_request, dummy_rec),
        ))

        cases.append((
            "Request Rejected (Patient)",
            sms_service._build_patient_rejected_message(dummy_request, "Insufficient stock for this blood group."),
        ))

        dummy_donation = SimpleNamespace(
            id=55,
            unit=450,
            bloodgroup="A+",
            donor=dummy_donor,
        )

        cases.append((
            "Donation Approved (Donor)",
            sms_service._build_donation_approved_message(dummy_donation),
        ))

        cases.append((
            "Donation Rejected (Donor)",
            sms_service._build_donation_rejected_message(dummy_donation, "Eligibility screening incomplete."),
        ))

        if max_count > 0:
            cases = cases[:max_count]

        masked = _mask_phone(normalized_to)
        self.stdout.write(f"Target: {masked}")
        self.stdout.write(f"Messages: {len(cases)}")

        if not apply_changes:
            self.stdout.write(self.style.WARNING("Preview only (no SMS sent). Use --apply to send."))
            for name, msg in cases:
                preview = msg.replace("\n", " ")
                if len(preview) > 180:
                    preview = preview[:177] + "…"
                self.stdout.write(f"- {name}: {preview}")
            return

        # Send
        sent = 0
        for name, msg in cases:
            payload = f"{name}: {msg}"
            result = send_sms(normalized_to, payload)
            status = result.get("status")
            if status == "success":
                sent += 1
            self.stdout.write(f"- {name}: {status} (duration_ms={result.get('duration_ms')})")

        self.stdout.write(self.style.SUCCESS(f"Done. Sent {sent}/{len(cases)} message(s)."))
