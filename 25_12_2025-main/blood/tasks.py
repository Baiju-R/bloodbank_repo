import logging

from celery import shared_task
from django.utils import timezone

from blood import models
from blood.services import sms as sms_service
from donor import models as donor_models


logger = logging.getLogger(__name__)


def _store_approval_sms_diagnostics(blood_request_id: int, result: dict) -> None:
    patient_to = (result.get('patient') or {}).get('to') or ''
    donor_to = (result.get('donor') or {}).get('to') or ''
    models.BloodRequest.objects.filter(pk=blood_request_id).update(
        sms_last_approval_attempt_at=timezone.now(),
        sms_last_approval_patient_to=str(patient_to)[:32],
        sms_last_approval_donor_to=str(donor_to)[:32],
        sms_last_approval_result=result,
    )


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={'max_retries': 3})
def send_request_approved_sms(self, blood_request_id: int) -> None:
    blood_request = models.BloodRequest.objects.select_related('patient', 'request_by_donor').get(pk=blood_request_id)
    result = sms_service.notify_request_approved(blood_request)
    if isinstance(result, dict):
        _store_approval_sms_diagnostics(blood_request_id, result)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={'max_retries': 3})
def send_request_rejected_sms(self, blood_request_id: int, reason: str = "") -> None:
    blood_request = models.BloodRequest.objects.select_related('patient', 'request_by_donor').get(pk=blood_request_id)
    sms_service.notify_request_rejected(blood_request, reason=reason or None)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={'max_retries': 3})
def send_donation_approved_sms(self, donation_id: int) -> None:
    donation = donor_models.BloodDonate.objects.select_related('donor').get(pk=donation_id)
    sms_service.notify_donation_approved(donation)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={'max_retries': 3})
def send_donation_rejected_sms(self, donation_id: int, reason: str = "") -> None:
    donation = donor_models.BloodDonate.objects.select_related('donor').get(pk=donation_id)
    sms_service.notify_donation_rejected(donation, reason=reason or None)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={'max_retries': 3})
def send_urgent_alerts(self, blood_request_id: int, contact_number: str = "") -> None:
    blood_request = models.BloodRequest.objects.get(pk=blood_request_id)
    sms_service.notify_matched_donors(blood_request, contact_number=contact_number or None)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={'max_retries': 3})
def send_requester_confirmation_sms(self, blood_request_id: int, contact_number: str = "") -> None:
    blood_request = models.BloodRequest.objects.get(pk=blood_request_id)
    sms_service.send_requester_confirmation(blood_request, contact_number or None)
