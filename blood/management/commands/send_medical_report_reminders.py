"""
Management command to send SMS/notification reminders to donors whose medical
reports are expiring soon or have already expired.

Run periodically (e.g., daily via cron):
    python manage.py send_medical_report_reminders

Options:
    --days-before  Number of days before expiry to start reminding (default: 14)
    --dry-run      Preview which donors would be notified without actually sending
"""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from donor.models import Donor, MedicalReport, MEDICAL_REPORT_VALIDITY_DAYS
from blood.models import InAppNotification
from blood.services import sms as sms_service

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Send reminders to donors with expiring or expired medical health reports.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days-before',
            type=int,
            default=14,
            help='Send reminders this many days before medical report expiry (default: 14)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview which donors would be notified without sending',
        )

    def handle(self, *args, **options):
        days_before = options['days_before']
        dry_run = options['dry_run']
        now = timezone.now()
        today = now.date()

        donors = Donor.objects.filter(is_approved=True, user__is_active=True).select_related('user')

        reminded_count = 0
        expired_count = 0

        for donor in donors:
            latest_report = donor.latest_medical_report

            if latest_report is None:
                # No report at all - send reminder
                self._send_reminder(donor, 'expired', dry_run)
                expired_count += 1
                continue

            expiry_date = latest_report.expiry_date
            days_remaining = (expiry_date - today).days

            if days_remaining < 0:
                # Already expired
                self._send_reminder(donor, 'expired', dry_run)
                expired_count += 1
            elif days_remaining <= days_before:
                # Expiring soon
                self._send_reminder(donor, 'expiring', dry_run, days_remaining=days_remaining)
                reminded_count += 1

        status = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(
            self.style.SUCCESS(
                f'{status}Processed: {reminded_count} expiring soon, '
                f'{expired_count} expired/missing reports.'
            )
        )

    def _send_reminder(self, donor, status, dry_run, days_remaining=None):
        """Send reminder notification and SMS to a donor."""
        if status == 'expired':
            title = 'Medical Report Expired'
            message = (
                f"Hello {donor.user.first_name}, your medical health report has expired "
                "or is missing. Please upload a new medical report to remain eligible "
                "for blood donation recommendations. Visit your dashboard > Medical Reports."
            )
            sms_text = (
                f"BloodBridge: {donor.user.first_name}, your medical report has expired. "
                "Please upload a new report to stay eligible for donations."
            )
        else:
            title = 'Medical Report Expiring Soon'
            message = (
                f"Hello {donor.user.first_name}, your medical health report expires in "
                f"{days_remaining} day(s). Please upload a new medical report before it "
                "expires to maintain your donation eligibility."
            )
            sms_text = (
                f"BloodBridge: {donor.user.first_name}, your medical report expires in "
                f"{days_remaining} day(s). Upload a new one to stay eligible."
            )

        if dry_run:
            self.stdout.write(
                f"  [DRY RUN] Would notify {donor.get_name} ({donor.mobile}): {title}"
            )
            return

        # In-app notification
        try:
            InAppNotification.objects.create(
                donor=donor,
                title=title,
                message=message,
            )
        except Exception:
            logger.exception("Failed to create medical report reminder for donor %s", donor.id)

        # SMS reminder (best-effort)
        try:
            from blood.utils.sms_sender import send_sms
            send_sms(donor.mobile, sms_text[:160])
        except Exception:
            logger.exception("Failed to send medical report reminder SMS to donor %s", donor.id)

        self.stdout.write(f"  Reminded: {donor.get_name} ({donor.mobile}) - {title}")
