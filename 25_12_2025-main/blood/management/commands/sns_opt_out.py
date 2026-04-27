from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from blood.utils.phone import normalize_phone_number

try:  # pragma: no cover
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:  # pragma: no cover
    boto3 = None

    class BotoCoreError(Exception):
        pass

    class ClientError(Exception):
        pass


class Command(BaseCommand):
    help = "Check (and optionally opt-in) phone numbers in the AWS SNS SMS opt-out list."

    def add_arguments(self, parser):
        parser.add_argument("phone", nargs="+", help="Phone number(s) to check (E.164 or local)")
        parser.add_argument(
            "--opt-in",
            action="store_true",
            help="If opted-out, attempt to opt the number back in via SNS.",
        )

    def handle(self, *args, **options):
        if boto3 is None:
            raise CommandError("boto3 is required (pip install boto3)")

        if not getattr(settings, "AWS_SNS_ENABLED", False):
            self.stdout.write(self.style.WARNING("AWS_SNS_ENABLED is False in settings; command will still query AWS."))

        region = getattr(settings, "AWS_SNS_REGION", None)
        if not region:
            raise CommandError("AWS_SNS_REGION is not set")

        sns = boto3.client("sns", region_name=region)

        opt_in = bool(options["opt_in"])

        for raw in options["phone"]:
            phone = normalize_phone_number(raw)
            if not phone:
                self.stdout.write(self.style.ERROR(f"INVALID: {raw!r}"))
                continue

            try:
                resp = sns.check_if_phone_number_is_opted_out(phoneNumber=phone)
                is_opted_out = bool(resp.get("isOptedOut"))
            except (ClientError, BotoCoreError) as exc:
                raise CommandError(f"Failed to check opt-out for {phone}: {exc}")

            if not is_opted_out:
                self.stdout.write(self.style.SUCCESS(f"OK (not opted out): {phone}"))
                continue

            self.stdout.write(self.style.WARNING(f"OPTED OUT: {phone}"))

            if opt_in:
                try:
                    sns.opt_in_phone_number(phoneNumber=phone)
                except (ClientError, BotoCoreError) as exc:
                    raise CommandError(f"Failed to opt-in {phone}: {exc}")

                # Re-check
                resp2 = sns.check_if_phone_number_is_opted_out(phoneNumber=phone)
                is_opted_out2 = bool(resp2.get("isOptedOut"))
                if is_opted_out2:
                    self.stdout.write(self.style.ERROR(f"Still opted out after opt-in attempt: {phone}"))
                else:
                    self.stdout.write(self.style.SUCCESS(f"Opt-in successful: {phone}"))
