"""Simple SNS SMS helper used by approval/rejection notifications.

This module intentionally keeps a small surface area and returns structured
dicts so callers can log or present outcomes without crashing the request.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from functools import lru_cache
from typing import Any, Dict

import boto3
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    NoCredentialsError,
    PartialCredentialsError,
)
from botocore.config import Config
from django.conf import settings

from blood.utils.phone import normalize_phone_number


logger = logging.getLogger(__name__)


_DISALLOWED_SMS_CHARS_TRANSLATION = str.maketrans({
    "[": "(",
    "]": ")",
    "{": "(",
    "}": ")",
    "<": "(",
    ">": ")",
    "\\": " ",
    "|": " ",
    "^": " ",
    "~": " ",
    "`": " ",
})


def sanitize_sms_text(text: str) -> str:
    """Normalize SMS text to be display-safe across devices/carriers.

    Some carriers mishandle special punctuation (including certain GSM-7 extension
    characters like '[' and ']'). We normalize to plain ASCII and replace a small
    set of problematic characters.
    """

    if text is None:
        return ""

    # Normalize Unicode -> ASCII.
    normalized = unicodedata.normalize("NFKD", str(text))
    normalized = normalized.encode("ascii", "ignore").decode("ascii", "ignore")

    # Collapse whitespace/newlines and replace problematic punctuation.
    normalized = normalized.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    normalized = normalized.translate(_DISALLOWED_SMS_CHARS_TRANSLATION)

    # Keep only common printable characters to reduce carrier rendering issues.
    normalized = re.sub(r"[^A-Za-z0-9 .,:;!?@#%&()\-+/=_']+", " ", normalized)
    normalized = re.sub(r"\s{2,}", " ", normalized).strip()
    return normalized


@lru_cache(maxsize=1)
def _get_sns_client():
    connect_timeout = int(getattr(settings, "AWS_SNS_CONNECT_TIMEOUT", 3))
    read_timeout = int(getattr(settings, "AWS_SNS_READ_TIMEOUT", 10))
    max_attempts = int(getattr(settings, "AWS_SNS_MAX_ATTEMPTS", 2))

    config = Config(
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        retries={"max_attempts": max_attempts, "mode": "standard"},
    )

    return boto3.client("sns", region_name=settings.AWS_SNS_REGION, config=config)


def check_sms_provider_health() -> Dict[str, Any]:
    """Validate AWS SNS readiness with lightweight API calls.

    Returns structured diagnostics so operators can quickly identify
    configuration/credential issues before attempting real notification sends.
    """

    enabled = bool(getattr(settings, "AWS_SNS_ENABLED", False))
    if not enabled:
        return {
            "ok": False,
            "status": "disabled",
            "reason": "AWS_SNS_ENABLED is false",
        }

    region = getattr(settings, "AWS_SNS_REGION", "")
    if not region:
        return {
            "ok": False,
            "status": "misconfigured",
            "reason": "AWS_SNS_REGION is not configured",
        }

    try:
        session = boto3.session.Session()
        creds = session.get_credentials()
        if creds is None:
            return {
                "ok": False,
                "status": "credentials-missing",
                "reason": "No AWS credentials found in environment/profile/role",
            }

        # Validate caller identity (fastest definitive auth check)
        sts = session.client("sts", region_name=region)
        identity = sts.get_caller_identity()

        # Validate SNS API permission without sending SMS.
        # Some IAM setups grant sns:Publish but not sns:GetSMSAttributes.
        # In that case, report degraded-but-usable health instead of hard-failing.
        sns = _get_sns_client()
        try:
            sns.get_sms_attributes(attributes=["DefaultSMSType"])
            return {
                "ok": True,
                "status": "healthy",
                "region": region,
                "account": identity.get("Account"),
                "arn": identity.get("Arn"),
            }
        except ClientError as exc:
            code = (exc.response or {}).get("Error", {}).get("Code", "ClientError")
            if code == "AuthorizationError":
                return {
                    "ok": True,
                    "status": "healthy-limited",
                    "region": region,
                    "account": identity.get("Account"),
                    "arn": identity.get("Arn"),
                    "warning": "Missing sns:GetSMSAttributes permission; publish may still work.",
                }
            raise
    except (NoCredentialsError, PartialCredentialsError) as exc:
        return {
            "ok": False,
            "status": "credentials-invalid",
            "reason": str(exc),
        }
    except ClientError as exc:
        code = (exc.response or {}).get("Error", {}).get("Code", "ClientError")
        message = (exc.response or {}).get("Error", {}).get("Message", str(exc))
        return {
            "ok": False,
            "status": "aws-client-error",
            "error_code": code,
            "reason": message,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "unknown-error",
            "reason": str(exc),
        }


def send_sms(phone: str, message: str):
    """Send a one-off SMS via AWS SNS."""

    if not bool(getattr(settings, "AWS_SNS_ENABLED", False)):
        return {
            "status": "skipped",
            "provider": "aws-sns",
            "reason": "sns-disabled",
        }

    normalized_phone = normalize_phone_number(phone)
    if not normalized_phone:
        return {
            "status": "error",
            "provider": "aws-sns",
            "error_code": "InvalidPhoneNumber",
            "message": "Phone number is missing/invalid; expected E.164 or local digits.",
        }

    sns = _get_sns_client()
    message = sanitize_sms_text(message)

    attributes = {
        'AWS.SNS.SMS.SMSType': {
            'DataType': 'String',
            'StringValue': getattr(settings, 'AWS_SNS_SMS_TYPE', 'Transactional'),
        }
    }
    sender_id = getattr(settings, 'AWS_SNS_SENDER_ID', '')
    if sender_id:
        attributes['AWS.SNS.SMS.SenderID'] = {
            'DataType': 'String',
            'StringValue': str(sender_id)[:11],
        }

    try:
        started = time.perf_counter()
        response = sns.publish(
            PhoneNumber=normalized_phone,
            Message=message,
            MessageAttributes=attributes,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        return {
            "status": "success",
            "provider": "aws-sns",
            "region": getattr(settings, "AWS_SNS_REGION", None),
            "duration_ms": duration_ms,
            "message_id": response.get("MessageId"),
            "response": response,
        }
    except (NoCredentialsError, PartialCredentialsError) as exc:
        logger.error("SNS credentials error while publishing to %s: %s", normalized_phone, exc)
        return {
            "status": "error",
            "provider": "aws-sns",
            "error_code": "CredentialsError",
            "message": str(exc),
        }
    except ClientError as exc:
        code = (exc.response or {}).get("Error", {}).get("Code", "ClientError")
        message = (exc.response or {}).get("Error", {}).get("Message", str(exc))
        logger.error("SNS publish failed to %s [%s]: %s", normalized_phone, code, message)
        return {
            "status": "error",
            "provider": "aws-sns",
            "error_code": code,
            "message": message,
        }
    except BotoCoreError as exc:
        logger.error("SNS publish transport failure to %s: %s", normalized_phone, exc)
        return {
            "status": "error",
            "provider": "aws-sns",
            "error_code": "BotoCoreError",
            "message": str(exc),
        }
