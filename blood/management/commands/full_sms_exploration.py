"""
Simulate two real users (donor + patient) exploring the ENTIRE BloodBridge
application and trigger EVERY SMS-producing operation.

Usage:
    python manage.py full_sms_exploration \
        --patient 9385425650 --donor 9361046558 --send-sms

This sends real SMS to both phones.  Without --send-sms it does a dry-run.
"""

from __future__ import annotations

import os
import time
from typing import Optional

from django.contrib.auth.models import User, Group
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from blood.models import BloodRequest, InAppNotification, Stock
from blood.services import sms as sms_service
from blood.utils.phone import normalize_phone_number
from blood.utils.sms_sender import check_sms_provider_health, send_sms
from donor.models import Donor, BloodDonate
from patient.models import Patient


# ── helpers ──────────────────────────────────────────────────────────────────

def _find_or_create_patient(raw_phone: str, first: str, last: str, bg: str) -> Patient:
    norm = normalize_phone_number(raw_phone)
    last10 = norm.lstrip("+")[-10:]
    patient = (
        Patient.objects.filter(mobile__icontains=last10)
        .select_related("user")
        .order_by("id")
        .first()
    )
    if patient:
        return patient

    username = f"smstest_p_{last10[-6:]}"
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"first_name": first, "last_name": last, "email": f"{username}@example.invalid"},
    )
    user.set_password("Test@12345")
    user.save()
    grp, _ = Group.objects.get_or_create(name="PATIENT")
    grp.user_set.add(user)

    patient, created = Patient.objects.get_or_create(
        user=user,
        defaults={
            "age": 28,
            "bloodgroup": bg,
            "disease": "General checkup",
            "doctorname": "Dr TestDoc",
            "address": "Chennai TN 600001",
            "mobile": raw_phone,
        },
    )
    if not created:
        Patient.objects.filter(pk=patient.pk).update(mobile=raw_phone)
        patient.refresh_from_db()
    return patient


def _find_or_create_donor(raw_phone: str, first: str, last: str, bg: str) -> Donor:
    norm = normalize_phone_number(raw_phone)
    last10 = norm.lstrip("+")[-10:]
    donor = (
        Donor.objects.filter(mobile__icontains=last10)
        .select_related("user")
        .order_by("id")
        .first()
    )
    if donor:
        # Make sure donor is eligible
        Donor.objects.filter(pk=donor.pk).update(
            is_available=True, last_notified_at=None, bloodgroup=bg,
        )
        donor.refresh_from_db()
        return donor

    username = f"smstest_d_{last10[-6:]}"
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"first_name": first, "last_name": last, "email": f"{username}@example.invalid"},
    )
    user.set_password("Test@12345")
    user.save()
    grp, _ = Group.objects.get_or_create(name="DONOR")
    grp.user_set.add(user)

    donor, created = Donor.objects.get_or_create(
        user=user,
        defaults={
            "bloodgroup": bg,
            "address": "Madurai TN 625001",
            "mobile": raw_phone,
            "zipcode": "600001",
            "is_available": True,
        },
    )
    if not created:
        Donor.objects.filter(pk=donor.pk).update(
            mobile=raw_phone, is_available=True, bloodgroup=bg, last_notified_at=None,
        )
        donor.refresh_from_db()
    return donor


def _ensure_stock():
    for bg in ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]:
        Stock.objects.get_or_create(bloodgroup=bg, defaults={"unit": 500})


class Command(BaseCommand):
    help = (
        "Simulate two real users exploring BloodBridge end-to-end, "
        "triggering every SMS-producing operation."
    )

    def add_arguments(self, parser):
        parser.add_argument("--patient", required=True, help="Patient phone (e.g. 9385425650)")
        parser.add_argument("--donor", required=True, help="Donor phone (e.g. 9361046558)")
        parser.add_argument(
            "--send-sms",
            action="store_true",
            help="Actually send SMS via AWS SNS (costs money).",
        )

    # ── main entry ───────────────────────────────────────────────────────
    def handle(self, *args, **options):
        patient_raw = options["patient"].strip()
        donor_raw = options["donor"].strip()
        live = bool(options.get("send_sms"))

        patient_e164 = normalize_phone_number(patient_raw)
        donor_e164 = normalize_phone_number(donor_raw)
        self.stdout.write(f"Patient phone: {patient_raw} → {patient_e164}")
        self.stdout.write(f"Donor phone  : {donor_raw} → {donor_e164}")
        self.stdout.write(f"Mode         : {'LIVE SMS' if live else 'DRY RUN'}")

        # Warn about env var override
        env_vars = [n for n in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY") if os.getenv(n)]
        if env_vars:
            self.stdout.write(self.style.WARNING(
                f"⚠ AWS credential env vars set ({', '.join(env_vars)}); "
                "clear them if you see InvalidClientTokenId."
            ))

        # AWS health
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("  AWS SNS HEALTH CHECK")
        self.stdout.write("=" * 70)
        health = check_sms_provider_health()
        for k in ["ok", "status", "region", "account", "arn"]:
            if health.get(k) not in (None, ""):
                self.stdout.write(f"  {k}: {health[k]}")
        if not health.get("ok") and live:
            self.stderr.write(self.style.ERROR("SMS provider unhealthy; aborting."))
            return

        # Seed stock & find/create users
        _ensure_stock()
        patient = _find_or_create_patient(patient_raw, "Josephin", "Mary", "O-")
        donor = _find_or_create_donor(donor_raw, "Ravi", "Kumar", "O-")
        self.stdout.write(f"\nPatient: {patient.get_name} (id={patient.id})")
        self.stdout.write(f"Donor  : {donor.get_name} (id={donor.id})")

        # Results accumulator
        results = []

        def record(scenario, target, result):
            status = "—"
            mid = ""
            if isinstance(result, dict):
                status = result.get("status", "?")
                mid = result.get("message_id", "")[:12]
            elif hasattr(result, "delivered"):
                status = f"delivered={result.delivered}"
            results.append((scenario, target, status, mid))
            self.stdout.write(f"  → {target}: {status}" + (f" [{mid}…]" if mid else ""))

        # ─────────────────────────────────────────────────────────────────
        # SCENARIO 1 — Patient submits URGENT O- request
        # Expected SMS: donor broadcast + requester confirmation
        # ─────────────────────────────────────────────────────────────────
        self._section("SCENARIO 1: Patient submits URGENT O- blood request")
        self.stdout.write("  Patient Josephin needs O- blood urgently for surgery.")
        self.stdout.write("  Expected SMS → donor phone (broadcast) + patient phone (confirmation)")

        req1 = self._create_request(patient, donor=None, bg="O-", unit=200, urgent=True)
        self._inapp(patient=patient, title="Request Submitted",
                    msg=f"Your urgent request #{req1.id} for O- (200ml) is pending.", req=req1)

        if live:
            # 1a: Donor broadcast
            broadcast = sms_service.notify_matched_donors(req1, contact_number=patient.mobile)
            record("1a-urgent-broadcast", f"DONOR {donor_e164}", broadcast)

            # 1b: Requester confirmation
            confirm = sms_service.send_requester_confirmation(req1, patient.mobile)
            record("1b-requester-confirm", f"PATIENT {patient_e164}", confirm)
        else:
            self.stdout.write("  (dry) would send: donor broadcast + requester confirmation")

        # ─────────────────────────────────────────────────────────────────
        # SCENARIO 2 — Admin APPROVES that urgent request
        # Expected SMS: patient approval + donor match
        # ─────────────────────────────────────────────────────────────────
        self._section("SCENARIO 2: Admin approves the urgent O- request")
        self.stdout.write("  Admin sees sufficient O- stock and approves.")
        self.stdout.write("  Expected SMS → patient phone (approved) + donor phone (you're a match)")

        self._approve_request(req1)
        self._inapp(patient=patient, title="Request Approved",
                    msg=f"Your request #{req1.id} for O- (200ml) has been approved.", req=req1)
        self._inapp(donor=donor, title="Match: Approved Request",
                    msg=f"You are top match for request #{req1.id} (O- 200ml). Check donor portal.", req=req1)

        if live:
            result = sms_service.notify_request_approved(req1)
            record("2a-approval-patient", f"PATIENT {patient_e164}",
                   result.get("patient") if isinstance(result, dict) else result)
            record("2b-approval-donor", f"DONOR {donor_e164}",
                   result.get("donor") if isinstance(result, dict) else result)
        else:
            self.stdout.write("  (dry) would send: approval SMS to patient + donor")

        # ─────────────────────────────────────────────────────────────────
        # SCENARIO 3 — Patient submits NON-URGENT B+ request
        # Expected SMS: requester confirmation only (NO donor broadcast)
        # ─────────────────────────────────────────────────────────────────
        self._section("SCENARIO 3: Patient submits non-urgent B+ request")
        self.stdout.write("  Patient needs B+ for scheduled transfusion (not urgent).")
        self.stdout.write("  Expected SMS → patient phone (confirmation only, no broadcast)")

        req2 = self._create_request(patient, donor=None, bg="B+", unit=100, urgent=False)
        self._inapp(patient=patient, title="Request Submitted",
                    msg=f"Your request #{req2.id} for B+ (100ml) is pending.", req=req2)

        if live:
            # No broadcast for non-urgent
            no_broadcast = sms_service.notify_matched_donors(req2, contact_number=patient.mobile)
            record("3a-no-broadcast", f"DONOR (skipped)", no_broadcast)

            confirm2 = sms_service.send_requester_confirmation(req2, patient.mobile)
            record("3b-confirm-non-urgent", f"PATIENT {patient_e164}", confirm2)
        else:
            self.stdout.write("  (dry) would send: confirmation only")

        # ─────────────────────────────────────────────────────────────────
        # SCENARIO 4 — Admin REJECTS the B+ request
        # Expected SMS: patient rejection
        # ─────────────────────────────────────────────────────────────────
        self._section("SCENARIO 4: Admin rejects B+ request (insufficient stock)")
        self.stdout.write("  Admin sees B+ stock is low and rejects the request.")
        self.stdout.write("  Expected SMS → patient phone (rejection)")

        req2.status = "Rejected"
        req2.save(update_fields=["status"])
        self._inapp(patient=patient, title="Request Rejected",
                    msg=f"Your request #{req2.id} for B+ (100ml) was rejected. Reason: low stock.", req=req2)

        if live:
            reject = sms_service.notify_request_rejected(req2, reason="B+ stock is currently low.")
            record("4-rejection-patient", f"PATIENT {patient_e164}", reject)
        else:
            self.stdout.write("  (dry) would send: rejection SMS")

        # ─────────────────────────────────────────────────────────────────
        # SCENARIO 5 — Patient submits URGENT A+ request
        # Expected SMS: donor broadcast + requester confirmation
        # ─────────────────────────────────────────────────────────────────
        self._section("SCENARIO 5: Patient submits URGENT A+ request (different blood group)")
        self.stdout.write("  Patient needs A+ blood urgently.")
        self.stdout.write("  Expected SMS → donor phone (if A+ match) + patient phone (confirmation)")

        # Temporarily make donor A+ eligible
        Donor.objects.filter(pk=donor.pk).update(bloodgroup="A+", last_notified_at=None)
        donor.refresh_from_db()

        req3 = self._create_request(patient, donor=None, bg="A+", unit=150, urgent=True)
        self._inapp(patient=patient, title="Request Submitted",
                    msg=f"Your urgent request #{req3.id} for A+ (150ml) is pending.", req=req3)

        if live:
            broadcast3 = sms_service.notify_matched_donors(req3, contact_number=patient.mobile)
            record("5a-urgent-A+-broadcast", f"DONOR {donor_e164}", broadcast3)

            confirm3 = sms_service.send_requester_confirmation(req3, patient.mobile)
            record("5b-confirm-A+", f"PATIENT {patient_e164}", confirm3)
        else:
            self.stdout.write("  (dry) would send: A+ broadcast + confirmation")

        # Restore donor to O-
        Donor.objects.filter(pk=donor.pk).update(bloodgroup="O-", last_notified_at=None)
        donor.refresh_from_db()

        # ─────────────────────────────────────────────────────────────────
        # SCENARIO 6 — Admin approves A+ request
        # Expected SMS: patient approval + donor match
        # ─────────────────────────────────────────────────────────────────
        self._section("SCENARIO 6: Admin approves A+ request")
        self.stdout.write("  Expected SMS → patient (approved) + donor (match)")

        # Make donor A+ again for matching
        Donor.objects.filter(pk=donor.pk).update(bloodgroup="A+", last_notified_at=None)
        donor.refresh_from_db()

        self._approve_request(req3)
        self._inapp(patient=patient, title="Request Approved",
                    msg=f"Your request #{req3.id} for A+ (150ml) approved.", req=req3)
        self._inapp(donor=donor, title="Match: Approved Request",
                    msg=f"You are top match for request #{req3.id} (A+ 150ml).", req=req3)

        if live:
            result6 = sms_service.notify_request_approved(req3)
            record("6a-approval-A+-patient", f"PATIENT {patient_e164}",
                   result6.get("patient") if isinstance(result6, dict) else result6)
            record("6b-approval-A+-donor", f"DONOR {donor_e164}",
                   result6.get("donor") if isinstance(result6, dict) else result6)
        else:
            self.stdout.write("  (dry) would send: A+ approval to both")

        # Restore donor
        Donor.objects.filter(pk=donor.pk).update(bloodgroup="O-", last_notified_at=None)
        donor.refresh_from_db()

        # ─────────────────────────────────────────────────────────────────
        # SCENARIO 7 — Donor donates 350ml O- blood, admin APPROVES
        # Expected SMS: donor approval
        # ─────────────────────────────────────────────────────────────────
        self._section("SCENARIO 7: Donor donates 350ml O- blood → Admin approves")
        self.stdout.write("  Donor Ravi walks in and donates 350ml of O- blood.")
        self.stdout.write("  Expected SMS → donor phone (donation approved, thank you)")

        don1 = BloodDonate.objects.create(
            donor=donor, disease="None", age=30,
            bloodgroup="O-", unit=350, status="Pending",
        )
        stock = Stock.objects.get(bloodgroup="O-")
        stock.unit += 350
        stock.save()
        don1.status = "Approved"
        don1.save(update_fields=["status"])

        self._inapp(donor=donor, title="Donation Approved",
                    msg=f"Your donation #{don1.id} of 350ml (O-) has been approved. Thank you!")

        if live:
            result7 = sms_service.notify_donation_approved(don1)
            record("7-donation-approved", f"DONOR {donor_e164}", result7)
        else:
            self.stdout.write("  (dry) would send: donation approved SMS")

        # ─────────────────────────────────────────────────────────────────
        # SCENARIO 8 — Donor donates again but admin REJECTS (mild fever)
        # Expected SMS: donor rejection
        # ─────────────────────────────────────────────────────────────────
        self._section("SCENARIO 8: Donor donates 200ml B+ → Admin rejects (mild fever)")
        self.stdout.write("  Donor tries to donate B+ but had mild fever.")
        self.stdout.write("  Expected SMS → donor phone (donation rejected)")

        don2 = BloodDonate.objects.create(
            donor=donor, disease="Mild fever", age=30,
            bloodgroup="B+", unit=200, status="Pending",
        )
        don2.status = "Rejected"
        don2.save(update_fields=["status"])

        self._inapp(donor=donor, title="Donation Rejected",
                    msg=f"Your donation #{don2.id} of 200ml (B+) was rejected. Reason: mild fever.")

        if live:
            result8 = sms_service.notify_donation_rejected(don2, reason="Donor had mild fever at screening.")
            record("8-donation-rejected", f"DONOR {donor_e164}", result8)
        else:
            self.stdout.write("  (dry) would send: donation rejected SMS")

        # ─────────────────────────────────────────────────────────────────
        # SCENARIO 9 — Donor makes a blood REQUEST (as donor, not patient)
        # Expected SMS: requester confirmation
        # ─────────────────────────────────────────────────────────────────
        self._section("SCENARIO 9: Donor makes blood request for family member")
        self.stdout.write("  Donor Ravi requests AB+ blood for a family member.")
        self.stdout.write("  Expected SMS → donor phone (requester confirmation)")

        req_by_donor = BloodRequest.objects.create(
            patient=None,
            request_by_donor=donor,
            patient_name="Ravi's Mother",
            patient_age=58,
            reason="Post-surgery transfusion for family",
            bloodgroup="AB+",
            unit=100,
            status="Pending",
            is_urgent=False,
            request_zipcode="600001",
        )
        self._inapp(donor=donor, title="Request Submitted",
                    msg=f"Your request #{req_by_donor.id} for AB+ (100ml) for family is pending.")

        if live:
            confirm_d = sms_service.send_requester_confirmation(req_by_donor, donor.mobile)
            record("9-donor-request-confirm", f"DONOR {donor_e164}", confirm_d)
        else:
            self.stdout.write("  (dry) would send: donor request confirmation")

        # ─────────────────────────────────────────────────────────────────
        # SCENARIO 10 — Admin approves donor's request
        # Expected SMS: requester (donor) approval
        # ─────────────────────────────────────────────────────────────────
        self._section("SCENARIO 10: Admin approves donor's AB+ family request")
        self.stdout.write("  Admin approves the AB+ request by donor.")
        self.stdout.write("  Expected SMS → donor phone (request approved)")

        self._approve_request(req_by_donor)
        self._inapp(donor=donor, title="Request Approved",
                    msg=f"Your request #{req_by_donor.id} for AB+ (100ml) has been approved.")

        if live:
            result10 = sms_service.notify_request_approved(req_by_donor)
            record("10a-donor-req-approved", f"DONOR {donor_e164}",
                   result10.get("patient") if isinstance(result10, dict) else result10)
            # "patient" key contains the requester result (which is the donor here)
        else:
            self.stdout.write("  (dry) would send: donor request approval SMS")

        # ─────────────────────────────────────────────────────────────────
        # SCENARIO 11 — Donor donates 450ml O+ blood → admin approves
        # Expected SMS: donor approval (different blood group)
        # ─────────────────────────────────────────────────────────────────
        self._section("SCENARIO 11: Donor donates 450ml O+ → Admin approves")
        self.stdout.write("  Donor donates directed O+ blood. Admin approves.")
        self.stdout.write("  Expected SMS → donor phone (donation approved)")

        don3 = BloodDonate.objects.create(
            donor=donor, disease="None", age=30,
            bloodgroup="O+", unit=450, status="Pending",
        )
        stock_op = Stock.objects.get(bloodgroup="O+")
        stock_op.unit += 450
        stock_op.save()
        don3.status = "Approved"
        don3.save(update_fields=["status"])

        self._inapp(donor=donor, title="Donation Approved",
                    msg=f"Your donation #{don3.id} of 450ml (O+) approved. Thank you!")

        if live:
            result11 = sms_service.notify_donation_approved(don3)
            record("11-donation-O+-approved", f"DONOR {donor_e164}", result11)
        else:
            self.stdout.write("  (dry) would send: O+ donation approved SMS")

        # ─────────────────────────────────────────────────────────────────
        # SCENARIO 12 — Patient submits URGENT AB- request → approve
        # Expected SMS: broadcast + confirmation + approval (both)
        # ─────────────────────────────────────────────────────────────────
        self._section("SCENARIO 12: Patient urgent AB- request → full cycle")
        self.stdout.write("  Patient needs AB- urgently. Full cycle: submit → approve.")
        self.stdout.write("  Expected SMS → broadcast + confirm + approval to both phones")

        Donor.objects.filter(pk=donor.pk).update(bloodgroup="AB-", last_notified_at=None)
        donor.refresh_from_db()

        req4 = self._create_request(patient, donor=None, bg="AB-", unit=250, urgent=True)
        self._inapp(patient=patient, title="Request Submitted",
                    msg=f"Urgent request #{req4.id} for AB- (250ml) submitted.", req=req4)

        if live:
            bc4 = sms_service.notify_matched_donors(req4, contact_number=patient.mobile)
            record("12a-AB--broadcast", f"DONOR {donor_e164}", bc4)

            cf4 = sms_service.send_requester_confirmation(req4, patient.mobile)
            record("12b-AB--confirm", f"PATIENT {patient_e164}", cf4)

        self._approve_request(req4)
        self._inapp(patient=patient, title="Request Approved",
                    msg=f"Request #{req4.id} for AB- (250ml) approved.", req=req4)
        self._inapp(donor=donor, title="Match: Approved Request",
                    msg=f"You are top match for request #{req4.id} (AB- 250ml).", req=req4)

        if live:
            result12 = sms_service.notify_request_approved(req4)
            record("12c-AB--appr-patient", f"PATIENT {patient_e164}",
                   result12.get("patient") if isinstance(result12, dict) else result12)
            record("12d-AB--appr-donor", f"DONOR {donor_e164}",
                   result12.get("donor") if isinstance(result12, dict) else result12)
        else:
            self.stdout.write("  (dry) would send: full cycle AB- SMS")

        # Restore donor
        Donor.objects.filter(pk=donor.pk).update(bloodgroup="O-", last_notified_at=None)

        # ─────────────────────────────────────────────────────────────────
        # SCENARIO 13 — Direct probe SMS to both phones
        # Expected SMS: 1 to patient + 1 to donor (raw send_sms)
        # ─────────────────────────────────────────────────────────────────
        self._section("SCENARIO 13: Direct probe SMS to both phones")
        self.stdout.write("  Sending a raw probe message to verify basic delivery.")

        if live:
            probe_p = send_sms(patient_e164, "BloodBridge: Probe test for patient phone. Ignore this message.")
            record("13a-probe-patient", f"PATIENT {patient_e164}", probe_p)

            probe_d = send_sms(donor_e164, "BloodBridge: Probe test for donor phone. Ignore this message.")
            record("13b-probe-donor", f"DONOR {donor_e164}", probe_d)
        else:
            self.stdout.write("  (dry) would send: probe to both phones")

        # ═════════════════════════════════════════════════════════════════
        # FINAL SUMMARY
        # ═════════════════════════════════════════════════════════════════
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("  FINAL SUMMARY")
        self.stdout.write("=" * 70)

        self.stdout.write(f"\n{'#':<6} {'Scenario':<30} {'Target':<28} {'Status':<15} {'MsgId'}")
        self.stdout.write("-" * 95)

        total = 0
        success = 0
        for i, (scenario, target, status, mid) in enumerate(results, 1):
            total += 1
            ok = "success" in str(status).lower() or "delivered=1" in str(status)
            if ok:
                success += 1
            marker = "✅" if ok else ("⏭" if "skip" in str(status).lower() or "delivered=0" in str(status) else "❌")
            self.stdout.write(
                f"{i:<6} {scenario:<30} {target:<28} {marker} {status:<12} {mid}"
            )

        self.stdout.write("-" * 95)
        self.stdout.write(f"Total: {total}  |  Success: {success}  |  Skipped/Other: {total - success}")

        # Count in-app notifications
        patient_notifs = InAppNotification.objects.filter(patient=patient).count()
        donor_notifs = InAppNotification.objects.filter(donor=donor).count()
        self.stdout.write(f"\nIn-app notifications: Patient={patient_notifs}  Donor={donor_notifs}")
        self.stdout.write("\nCheck both phones for SMS delivery. Check dashboards for in-app notifications.")

    # ── utility methods ──────────────────────────────────────────────────

    def _section(self, title: str):
        self.stdout.write(f"\n{'─' * 70}")
        self.stdout.write(f"  {title}")
        self.stdout.write(f"{'─' * 70}")

    def _create_request(self, patient, *, donor=None, bg: str, unit: int, urgent: bool) -> BloodRequest:
        req = BloodRequest.objects.create(
            patient=patient,
            request_by_donor=None,
            patient_name=patient.get_name,
            patient_age=patient.age,
            reason=f"SMS exploration test – {bg} {'(URGENT)' if urgent else '(routine)'}",
            bloodgroup=bg,
            unit=unit,
            status="Pending",
            is_urgent=urgent,
            request_zipcode="600001",
        )
        self.stdout.write(f"  Created BloodRequest #{req.id} [{bg}, {unit}ml, urgent={urgent}]")
        return req

    def _approve_request(self, req: BloodRequest):
        stock = Stock.objects.get(bloodgroup=req.bloodgroup)
        if stock.unit >= req.unit:
            stock.unit -= req.unit
            stock.save()
        req.status = "Approved"
        req.save(update_fields=["status"])
        self.stdout.write(f"  Approved BloodRequest #{req.id}")

    def _inapp(self, *, patient=None, donor=None, title: str, msg: str, req=None):
        InAppNotification.objects.create(
            patient=patient,
            donor=donor,
            title=title[:120],
            message=msg,
            related_request=req,
        )
