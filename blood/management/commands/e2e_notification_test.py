from __future__ import annotations

import os
from typing import Optional

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from blood.models import BloodRequest, InAppNotification
from blood.services import sms as sms_service
from blood.utils.phone import normalize_phone_number
from blood.utils.sms_sender import check_sms_provider_health, send_sms
from donor.models import Donor, BloodDonate
from patient.models import Patient


def _find_patient_by_phone(raw_phone: str) -> Optional[Patient]:
    normalized = normalize_phone_number(raw_phone)
    if not normalized:
        return None
    last10 = normalized.lstrip("+")[-10:]
    return (
        Patient.objects.filter(mobile__icontains=last10)
        .select_related("user")
        .order_by("id")
        .first()
    )


def _find_donor_by_phone(raw_phone: str) -> Optional[Donor]:
    normalized = normalize_phone_number(raw_phone)
    if not normalized:
        return None
    last10 = normalized.lstrip("+")[-10:]
    return (
        Donor.objects.filter(mobile__icontains=last10)
        .select_related("user")
        .order_by("id")
        .first()
    )


def _maybe_create_patient(*, raw_phone: str, bloodgroup: str) -> Patient:
    normalized = normalize_phone_number(raw_phone)
    if not normalized:
        raise ValueError("Invalid patient phone")

    username = f"patient_{normalized.lstrip('+')[-8:]}"
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={
            "first_name": "E2E",
            "last_name": "Patient",
            "email": f"{username}@example.invalid",
        },
    )

    patient, created = Patient.objects.get_or_create(
        user=user,
        defaults={
            "age": 30,
            "bloodgroup": bloodgroup,
            "disease": "E2E test",
            "doctorname": "Dr Test",
            "address": "E2E Address",
            "mobile": raw_phone,
        },
    )
    if not created:
        Patient.objects.filter(pk=patient.pk).update(mobile=raw_phone)
        patient.refresh_from_db()
    return patient


def _maybe_create_donor(*, raw_phone: str, bloodgroup: str) -> Donor:
    normalized = normalize_phone_number(raw_phone)
    if not normalized:
        raise ValueError("Invalid donor phone")

    username = f"donor_{normalized.lstrip('+')[-8:]}"
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={
            "first_name": "E2E",
            "last_name": "Donor",
            "email": f"{username}@example.invalid",
        },
    )

    donor, created = Donor.objects.get_or_create(
        user=user,
        defaults={
            "bloodgroup": bloodgroup,
            "address": "E2E Address",
            "mobile": raw_phone,
            "zipcode": "560001",
            "is_available": True,
        },
    )
    if not created:
        Donor.objects.filter(pk=donor.pk).update(mobile=raw_phone, is_available=True, bloodgroup=bloodgroup)
        donor.refresh_from_db()
    return donor


class Command(BaseCommand):
    help = "End-to-end notification test (in-app + SMS) for a given patient & donor phone."

    def add_arguments(self, parser):
        parser.add_argument("--patient", required=True, help="Patient mobile (e.g., 9385425650)")
        parser.add_argument("--donor", required=True, help="Donor mobile (e.g., 9361046558)")
        parser.add_argument("--bloodgroup", default="O-", help="Blood group to use for the test request")
        parser.add_argument(
            "--isolate",
            action="store_true",
            help="Use a temporary unique blood group during the test so only the provided donor is targeted.",
        )
        parser.add_argument("--unit", default=100, type=int, help="Unit amount (ml) for the test request")
        parser.add_argument("--urgent", action="store_true", help="Create an urgent request (sends urgent donor alert)")
        parser.add_argument(
            "--send-sms",
            action="store_true",
            help="Actually send SMS via AWS SNS (costs money). Without this flag, prints what would be sent.",
        )
        parser.add_argument(
            "--create-missing",
            action="store_true",
            help="Create patient/donor records if the provided phones are not found.",
        )

    def handle(self, *args, **options):
        patient_raw = str(options["patient"]).strip()
        donor_raw = str(options["donor"]).strip()
        bloodgroup = str(options["bloodgroup"]).strip() or "O-"
        isolate = bool(options.get("isolate"))
        unit = int(options["unit"])
        urgent = bool(options["urgent"])
        send_sms_flag = bool(options["send_sms"])
        create_missing = bool(options["create_missing"])

        self.stdout.write("=== 0) Normalize phone numbers ===")
        patient_e164 = normalize_phone_number(patient_raw)
        donor_e164 = normalize_phone_number(donor_raw)
        self.stdout.write(f"patient: {patient_raw} -> {patient_e164}")
        self.stdout.write(f"donor  : {donor_raw} -> {donor_e164}")
        if not patient_e164 or not donor_e164:
            self.stderr.write(self.style.ERROR("Invalid phone(s)."))
            return

        env_credential_vars = [
            name
            for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")
            if os.getenv(name)
        ]
        if env_credential_vars:
            self.stdout.write(
                self.style.WARNING(
                    "AWS credential environment variables are set (these override `aws configure`): "
                    + ", ".join(env_credential_vars)
                )
            )
            self.stdout.write(
                self.style.WARNING(
                    "If you see InvalidClientTokenId, clear them in this PowerShell session with: "
                    "Remove-Item Env:AWS_ACCESS_KEY_ID, Env:AWS_SECRET_ACCESS_KEY, Env:AWS_SESSION_TOKEN -ErrorAction SilentlyContinue"
                )
            )

        self.stdout.write("\n=== 1) AWS SNS health ===")
        health = check_sms_provider_health()
        for key in ["ok", "status", "reason", "region", "account", "arn", "error_code"]:
            if key in health and health.get(key) not in (None, ""):
                self.stdout.write(f"- {key}: {health.get(key)}")
        if not health.get("ok") and send_sms_flag:
            self.stderr.write(self.style.ERROR("SMS provider is NOT healthy; aborting send."))
            return

        self.stdout.write("\n=== 2) Find patient/donor records by phone ===")
        patient = _find_patient_by_phone(patient_raw)
        donor = _find_donor_by_phone(donor_raw)

        if not patient and create_missing:
            patient = _maybe_create_patient(raw_phone=patient_raw, bloodgroup=bloodgroup)
        if not donor and create_missing:
            donor = _maybe_create_donor(raw_phone=donor_raw, bloodgroup=bloodgroup)

        self.stdout.write(f"patient_found: {bool(patient)} id={getattr(patient, 'id', None)} username={getattr(getattr(patient, 'user', None), 'username', None)}")
        self.stdout.write(f"donor_found  : {bool(donor)} id={getattr(donor, 'id', None)} username={getattr(getattr(donor, 'user', None), 'username', None)}")

        if not patient or not donor:
            self.stderr.write(
                self.style.ERROR(
                    "Patient/Donor records not found. Re-run with --create-missing if you want the command to create them."
                )
            )
            return

        # Optional isolation: prevent notifying other real donors by using a unique bloodgroup
        # for the duration of the test, then restoring the donor profile.
        original_bloodgroup = donor.bloodgroup
        test_bloodgroup = bloodgroup
        if isolate:
            suffix = timezone.now().strftime("%m%d%H%M")
            test_bloodgroup = f"{bloodgroup}-E2E-{suffix}"[:10]
            self.stdout.write(self.style.WARNING(f"isolate=ON -> using test_bloodgroup={test_bloodgroup!r} (will restore donor after test)"))

        # Ensure our provided donor is eligible and not cooldown-blocked.
        Donor.objects.filter(pk=donor.pk).update(
            is_available=True,
            last_notified_at=None,
            bloodgroup=test_bloodgroup,
            zipcode=(donor.zipcode or "560001"),
        )
        donor.refresh_from_db()

        results = {}

        self.stdout.write("\n=== 3) Urgent request: in-app + donor broadcast SMS + requester confirmation ===")
        with transaction.atomic():
            req_urgent = BloodRequest.objects.create(
                patient=patient,
                request_by_donor=None,
                patient_name=patient.get_name,
                patient_age=patient.age,
                reason=f"E2E urgent notification test at {timezone.now().isoformat()}",
                bloodgroup=test_bloodgroup,
                unit=unit,
                status="Pending",
                is_urgent=True,
                request_zipcode="560001",
            )
            InAppNotification.objects.create(
                patient=patient,
                related_request=req_urgent,
                title="Blood Request Submitted",
                message=f"Your blood request #{req_urgent.id} for {unit}ml ({test_bloodgroup}) has been submitted and is pending review.",
            )
        self.stdout.write(f"urgent_request_id: {req_urgent.id}")

        if send_sms_flag:
            alert_result = sms_service.notify_matched_donors(req_urgent, contact_number=patient.mobile)
            results["urgent_broadcast"] = {
                "enabled": alert_result.enabled,
                "attempted": alert_result.attempted,
                "delivered": alert_result.delivered,
                "recipients": alert_result.recipients,
                "skipped": alert_result.skipped,
                "reason": alert_result.reason,
            }
            self.stdout.write(f"urgent_broadcast: attempted={alert_result.attempted} delivered={alert_result.delivered} reason={alert_result.reason}")
            requester_result = sms_service.send_requester_confirmation(req_urgent, patient.mobile)
            results["urgent_requester_confirmation"] = requester_result
            self.stdout.write(f"requester_confirmation: {requester_result}")
        else:
            self.stdout.write("(dry) would_call: sms_service.notify_matched_donors + send_requester_confirmation")

        self.stdout.write("\n=== 4) Approval SMS scenario (Approved) ===")
        BloodRequest.objects.filter(pk=req_urgent.pk).update(status="Approved")
        req_urgent.refresh_from_db()
        InAppNotification.objects.create(
            patient=patient,
            related_request=req_urgent,
            title="Blood Request Approved",
            message=f"Your request #{req_urgent.id} for {test_bloodgroup} ({unit}ml) has been approved.",
        )
        if send_sms_flag:
            approval_result = sms_service.notify_request_approved(req_urgent)
            results["approval"] = approval_result
            self.stdout.write(f"approval_sms_status: {approval_result.get('status') if isinstance(approval_result, dict) else approval_result}")
            if isinstance(approval_result, dict):
                self.stdout.write(f"patient_sms: {approval_result.get('patient')}")
                self.stdout.write(f"donor_sms: {approval_result.get('donor')}")
        else:
            self.stdout.write("(dry) would_call: sms_service.notify_request_approved")

        self.stdout.write("\n=== 5) Rejection SMS scenario (Rejected) ===")
        req_reject = BloodRequest.objects.create(
            patient=patient,
            request_by_donor=None,
            patient_name=patient.get_name,
            patient_age=patient.age,
            reason=f"E2E rejection test at {timezone.now().isoformat()}",
            bloodgroup=test_bloodgroup,
            unit=unit,
            status="Rejected",
            is_urgent=False,
            request_zipcode="560001",
        )
        InAppNotification.objects.create(
            patient=patient,
            related_request=req_reject,
            title="Blood Request Rejected",
            message=f"Your request #{req_reject.id} for {test_bloodgroup} ({unit}ml) was rejected.",
        )
        if send_sms_flag:
            reject_result = sms_service.notify_request_rejected(req_reject, reason="E2E rejection test")
            results["rejection"] = reject_result
            self.stdout.write(f"rejection_sms: {reject_result}")
        else:
            self.stdout.write("(dry) would_call: sms_service.notify_request_rejected")

        self.stdout.write("\n=== 6) Donation approved + rejected SMS scenarios ===")
        donation_approved = BloodDonate.objects.create(
            donor=donor,
            disease="E2E",
            age=30,
            bloodgroup=test_bloodgroup,
            unit=unit,
            status="Approved",
        )
        InAppNotification.objects.create(
            donor=donor,
            title="Donation Approved",
            message=f"Your donation #{donation_approved.id} ({unit}ml, {test_bloodgroup}) has been approved.",
        )
        if send_sms_flag:
            donation_approved_result = sms_service.notify_donation_approved(donation_approved)
            results["donation_approved"] = donation_approved_result
            self.stdout.write(f"donation_approved_sms: {donation_approved_result}")
        else:
            self.stdout.write("(dry) would_call: sms_service.notify_donation_approved")

        donation_rejected = BloodDonate.objects.create(
            donor=donor,
            disease="E2E",
            age=30,
            bloodgroup=test_bloodgroup,
            unit=unit,
            status="Rejected",
        )
        InAppNotification.objects.create(
            donor=donor,
            title="Donation Rejected",
            message=f"Your donation #{donation_rejected.id} ({unit}ml, {test_bloodgroup}) was rejected.",
        )
        if send_sms_flag:
            donation_rejected_result = sms_service.notify_donation_rejected(donation_rejected, reason="E2E donation rejection")
            results["donation_rejected"] = donation_rejected_result
            self.stdout.write(f"donation_rejected_sms: {donation_rejected_result}")
        else:
            self.stdout.write("(dry) would_call: sms_service.notify_donation_rejected")

        self.stdout.write("\n=== 7) Summary ===")
        self.stdout.write(f"urgent_request_id={req_urgent.id} rejection_request_id={req_reject.id}")
        self.stdout.write(f"donation_approved_id={donation_approved.id} donation_rejected_id={donation_rejected.id}")
        self.stdout.write("SMS results keys: " + ", ".join(sorted(results.keys())))
        self.stdout.write("Check patient & donor dashboards for in-app notifications.")

        # Restore donor bloodgroup if we isolated.
        if isolate:
            Donor.objects.filter(pk=donor.pk).update(bloodgroup=original_bloodgroup)
