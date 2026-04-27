from __future__ import annotations

from django.core.management.base import BaseCommand

from blood.utils.phone import normalize_phone_number
from blood.utils.sms_sender import check_sms_provider_health, send_sms


class Command(BaseCommand):
    help = "Validate AWS SNS SMS configuration and optionally send one probe SMS."

    def add_arguments(self, parser):
        parser.add_argument(
            "--to",
            type=str,
            default="",
            help="Optional destination number to send a probe SMS (e.g., 9361046558).",
        )
        parser.add_argument(
            "--probe",
            action="store_true",
            help="Actually send a probe SMS when --to is provided.",
        )

    def handle(self, *args, **options):
        health = check_sms_provider_health()

        self.stdout.write("SMS provider health:")
        for key in ["ok", "status", "reason", "region", "account", "arn", "error_code"]:
            if key in health and health.get(key) not in (None, ""):
                self.stdout.write(f"- {key}: {health.get(key)}")

        if not health.get("ok"):
            self.stderr.write(self.style.ERROR("SMS provider is NOT healthy."))
            return

        raw_to = (options.get("to") or "").strip()
        do_probe = bool(options.get("probe"))

        if not raw_to:
            self.stdout.write(self.style.SUCCESS("Health check passed. No probe SMS requested."))
            return

        normalized = normalize_phone_number(raw_to)
        if not normalized:
            self.stderr.write(self.style.ERROR("Invalid destination number for probe."))
            return

        if not do_probe:
            self.stdout.write(
                self.style.WARNING(
                    f"Probe target normalized to {normalized}. Re-run with --probe to send one SMS."
                )
            )
            return

        result = send_sms(normalized, "BloodBridge SMS probe: provider health check successful.")
        status = result.get("status")
        self.stdout.write(f"probe_status: {status}")
        if result.get("message_id"):
            self.stdout.write(f"message_id: {result.get('message_id')}")
        if result.get("error_code"):
            self.stdout.write(f"error_code: {result.get('error_code')}")
        if result.get("message"):
            self.stdout.write(f"message: {result.get('message')}")

        if status == "success":
            self.stdout.write(self.style.SUCCESS("Probe SMS sent successfully."))
        else:
            self.stderr.write(self.style.ERROR("Probe SMS failed."))
