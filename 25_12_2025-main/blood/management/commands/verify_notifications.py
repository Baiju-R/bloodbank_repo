from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.test.utils import override_settings
from django.utils import timezone

from blood import models as blood_models
from blood.services import sms as sms_service
from donor import models as donor_models
from patient import models as patient_models


@dataclass
class _Result:
	name: str
	ok: bool
	detail: str = ""


class _StubSNSClient:
	def __init__(self):
		self.publish_calls: list[dict[str, Any]] = []

	def publish(self, **kwargs):
		self.publish_calls.append(dict(kwargs))
		return {"MessageId": f"stub-{len(self.publish_calls)}"}


def _stub_sms_sender(phone: str, message: str):
	return {"status": "success", "to": phone, "preview": (message or "")[:70]}


class Command(BaseCommand):
	help = (
		"Smoke-test notification flows (in-app + SMS) locally without sending real SMS. "
		"Creates temporary records in the local DB."
	)

	def handle(self, *args, **options):
		started = time.perf_counter()
		stamp = int(time.time())
		results: list[_Result] = []

		# --- Arrange: create a donor + patient + request ---
		donor_user = User.objects.create_user(
			username=f"notify_donor_{stamp}",
			password="DemoPass123!",
			first_name="Notify",
			last_name="Donor",
		)
		donor = donor_models.Donor.objects.create(
			user=donor_user,
			bloodgroup="A+",
			address="221B Baker Street",
			mobile="+1 (555) 123-4567",
			zipcode="560001",
		)

		patient_user = User.objects.create_user(
			username=f"notify_patient_{stamp}",
			password="DemoPass123!",
			first_name="Notify",
			last_name="Patient",
		)
		patient = patient_models.Patient.objects.create(
			user=patient_user,
			age=30,
			bloodgroup="A+",
			disease="None",
			doctorname="Dr. Demo",
			address="Demo Address",
			mobile="+1 (555) 999-0000",
		)

		blood_request = blood_models.BloodRequest.objects.create(
			patient=patient,
			request_by_donor=None,
			patient_name=patient.get_name,
			patient_age=patient.age,
			reason="Need blood for demo notification verification",
			bloodgroup="A+",
			unit=250,
			status="Pending",
			is_urgent=True,
			request_zipcode="560001",
		)

		# --- In-app notifications ---
		before_count = blood_models.InAppNotification.objects.count()
		blood_models.InAppNotification.objects.create(
			patient=patient,
			related_request=blood_request,
			title="Smoke Test: Request Submitted",
			message=f"Request #{blood_request.id} created.",
		)
		after_count = blood_models.InAppNotification.objects.count()
		results.append(
			_Result(
				name="inapp.create",
				ok=(after_count == before_count + 1),
				detail=f"count_before={before_count} count_after={after_count}",
			)
		)

		# --- Appointment notification (in-app) ---
		slot = blood_models.DonationAppointmentSlot.objects.create(
			start_at=timezone.now() + timezone.timedelta(hours=2),
			end_at=timezone.now() + timezone.timedelta(hours=3),
			capacity=5,
			is_active=True,
		)
		appointment = blood_models.DonationAppointment.objects.create(
			donor=donor,
			slot=slot,
			requested_for=slot.start_at,
			status=blood_models.DonationAppointment.STATUS_PENDING,
		)
		blood_models.InAppNotification.objects.create(
			donor=donor,
			title="Smoke Test: Appointment",
			message=f"Appointment #{appointment.id} pending.",
		)
		results.append(_Result("inapp.appointment", True, f"appointment_id={appointment.id}"))

		# --- Verification badge notification (in-app) ---
		blood_models.VerificationBadge.objects.update_or_create(
			donor=donor,
			patient=None,
			defaults={
				"badge_name": "Verified Identity",
				"hospital_name": "Demo Hospital",
				"is_verified": True,
				"trust_score": 80,
				"verified_at": timezone.now(),
			},
		)
		blood_models.InAppNotification.objects.create(
			donor=donor,
			title="Smoke Test: Verification Badge Updated",
			message="Verification updated.",
		)
		results.append(_Result("inapp.verification", True, "badge_updated"))

		# --- SMS flows (no real AWS calls) ---
		with override_settings(
			AWS_SNS_ENABLED=True,
			AWS_SNS_DEFAULT_COUNTRY_CODE="+1",
			AWS_SNS_MAX_RECIPIENTS=5,
			AWS_SNS_MIN_NOTIFICATION_GAP_SECONDS=0,
		):
			stub_sns = _StubSNSClient()
			alert = sms_service.notify_matched_donors(blood_request, contact_number=patient.mobile, sns_client=stub_sns)
			results.append(
				_Result(
					"sms.urgent_alerts",
					ok=(alert.delivered >= 1 and len(stub_sns.publish_calls) == alert.delivered),
					detail=f"attempted={alert.attempted} delivered={alert.delivered}",
				)
			)

			confirm = sms_service.send_requester_confirmation(
				blood_request,
				patient.mobile,
				sms_sender=_stub_sms_sender,
			)
			results.append(_Result("sms.requester_confirmation", confirm.get("status") == "success", str(confirm)))

			approved = sms_service.notify_request_approved(blood_request, sms_sender=_stub_sms_sender)
			results.append(_Result("sms.request_approved", approved.get("status") == "sent", str(approved)))

			rejected = sms_service.notify_request_rejected(
				blood_request,
				reason="Smoke test rejection",
				sms_sender=_stub_sms_sender,
			)
			results.append(_Result("sms.request_rejected", rejected.get("status") == "success", str(rejected)))

			donation = donor_models.BloodDonate.objects.create(
				donor=donor,
				disease="None",
				age=30,
				bloodgroup="A+",
				unit=250,
				status="Pending",
			)
			don_approved = sms_service.notify_donation_approved(donation, sms_sender=_stub_sms_sender)
			results.append(_Result("sms.donation_approved", don_approved.get("status") == "success", str(don_approved)))

			don_rejected = sms_service.notify_donation_rejected(donation, reason="Smoke test", sms_sender=_stub_sms_sender)
			results.append(_Result("sms.donation_rejected", don_rejected.get("status") == "success", str(don_rejected)))

		# --- Summary ---
		failed = [r for r in results if not r.ok]
		duration_ms = int((time.perf_counter() - started) * 1000)

		self.stdout.write("Notification smoke test results:")
		for r in results:
			prefix = "OK" if r.ok else "FAIL"
			self.stdout.write(f"- {prefix} {r.name}: {r.detail}")

		self.stdout.write(f"duration_ms: {duration_ms}")
		if failed:
			raise SystemExit(1)
		self.stdout.write(self.style.SUCCESS("All notification smoke tests passed."))
