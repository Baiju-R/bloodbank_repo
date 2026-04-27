from __future__ import annotations

import re
from typing import Optional

from django.conf import settings


def normalize_phone_number(raw: Optional[str]) -> Optional[str]:
    """Normalize a phone number to E.164 format.

    Accepts inputs like:
    - "+919385426550"
    - "9385426550" (uses AWS_SNS_DEFAULT_COUNTRY_CODE)
    - "+1 (555) 111-2222"

    Returns None if the number is missing/invalid.
    """

    if not raw:
        return None

    cleaned = re.sub(r"[\s\-()]+", "", str(raw).strip())

    if cleaned.startswith("+"):
        digits = "+" + re.sub(r"[^0-9]", "", cleaned)
        return digits if len(digits) >= 8 else None

    digits_only = re.sub(r"[^0-9]", "", cleaned)
    if not digits_only:
        return None

    if digits_only.startswith("0"):
        digits_only = digits_only.lstrip("0") or digits_only

    default_code = getattr(settings, "AWS_SNS_DEFAULT_COUNTRY_CODE", None) or "+1"
    if not str(default_code).startswith("+"):
        default_code = f"+{default_code}"

    # If user already typed country code without '+'
    if digits_only.startswith(str(default_code).lstrip("+")):
        return f"+{digits_only}"

    return f"{default_code}{digits_only}"
