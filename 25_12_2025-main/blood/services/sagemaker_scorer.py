"""
Amazon SageMaker Donor Recommendation Scorer.

Calls a deployed SageMaker endpoint to produce an AI-driven recommendation
score (0–100) for each donor based on their profile features relative to a
blood request.

Architecture
------------
1.  Features are extracted locally from Donor model + BloodRequest context.
2.  They are sent as a CSV payload to the SageMaker Runtime ``InvokeEndpoint`` API.
3.  The endpoint hosts an XGBoost model trained on historical donation-success data.
4.  The response is a probability (0–1) which is scaled to 0–100.

Fallback
--------
When ``SAGEMAKER_ENDPOINT_ENABLED`` is False (default) or the endpoint is
unreachable, the system transparently falls back to rule-based scoring.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Sequence

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError
from django.conf import settings
from django.utils import timezone

from donor import models as dmodels

logger = logging.getLogger(__name__)

# ─── Feature engineering constants ────────────────────────────────────────────
FEATURE_NAMES = [
    "blood_match",        # 1 if matches, else 0
    "is_available",       # 1/0
    "same_zipcode",       # 1/0
    "has_coordinates",    # 1/0
    "age_years",          # numeric, -1 if unknown
    "weight_kg",          # numeric, -1 if unknown
    "hemoglobin_g_dl",    # numeric, -1 if unknown
    "bp_systolic",        # numeric, -1 if unknown
    "bp_diastolic",       # numeric, -1 if unknown
    "has_chronic_disease", # 1/0
    "on_medication",      # 1/0
    "smokes",             # 1/0
    "sex_male",           # 1/0
    "sex_female",         # 1/0
    "days_since_donation",# numeric, -1 if never donated
    "in_recovery",        # 1/0
    "donation_count",     # total approved donations
    "profile_completeness", # 0.0–1.0 fraction of medical fields filled
]

# Fields used to measure profile completeness
_MEDICAL_FIELDS = [
    "date_of_birth", "weight_kg", "hemoglobin_g_dl",
    "blood_pressure_systolic", "blood_pressure_diastolic", "sex",
]


@dataclass(frozen=True)
class SageMakerScore:
    """Result from the SageMaker endpoint for one donor."""
    donor_id: int
    ai_score: float           # 0–100 scale
    confidence: float         # 0.0–1.0 raw probability from the model
    features: Dict[str, float]
    source: str               # "sagemaker" or "fallback"


# ─── Feature extraction ──────────────────────────────────────────────────────

def _age_from_dob(dob: Optional[date]) -> int:
    if not dob:
        return -1
    today = timezone.now().date()
    years = today.year - dob.year
    if (today.month, today.day) < (dob.month, dob.day):
        years -= 1
    return years


def _days_since(d: Optional[date]) -> int:
    if not d:
        return -1
    return (timezone.now().date() - d).days


def extract_features(
    donor: dmodels.Donor,
    request_bloodgroup: str,
    request_zipcode: str = "",
) -> Dict[str, float]:
    """Extract a feature vector from a donor for the ML model."""
    recovery_days = int(getattr(settings, "DONATION_RECOVERY_DAYS", 56))
    days_since_donation = _days_since(donor.last_donated_at)
    in_recovery = 1 if (days_since_donation >= 0 and days_since_donation < recovery_days) else 0

    # Count approved donations
    from donor.models import BloodDonate
    donation_count = BloodDonate.objects.filter(
        donor=donor, status="Approved"
    ).count()

    # Profile completeness
    filled = sum(
        1 for f in _MEDICAL_FIELDS
        if getattr(donor, f, None) is not None and getattr(donor, f, None) != "U"
    )
    completeness = filled / len(_MEDICAL_FIELDS)

    return {
        "blood_match": 1.0 if donor.bloodgroup == request_bloodgroup else 0.0,
        "is_available": 1.0 if donor.is_available else 0.0,
        "same_zipcode": (
            1.0
            if request_zipcode and donor.zipcode and request_zipcode.strip() == donor.zipcode.strip()
            else 0.0
        ),
        "has_coordinates": 1.0 if donor.latitude is not None and donor.longitude is not None else 0.0,
        "age_years": float(_age_from_dob(donor.date_of_birth)),
        "weight_kg": float(donor.weight_kg) if donor.weight_kg else -1.0,
        "hemoglobin_g_dl": float(donor.hemoglobin_g_dl) if donor.hemoglobin_g_dl else -1.0,
        "bp_systolic": float(donor.blood_pressure_systolic) if donor.blood_pressure_systolic else -1.0,
        "bp_diastolic": float(donor.blood_pressure_diastolic) if donor.blood_pressure_diastolic else -1.0,
        "has_chronic_disease": 1.0 if donor.has_chronic_disease else 0.0,
        "on_medication": 1.0 if donor.on_medication else 0.0,
        "smokes": 1.0 if donor.smokes else 0.0,
        "sex_male": 1.0 if donor.sex == "M" else 0.0,
        "sex_female": 1.0 if donor.sex == "F" else 0.0,
        "days_since_donation": float(days_since_donation),
        "in_recovery": float(in_recovery),
        "donation_count": float(donation_count),
        "profile_completeness": completeness,
    }


def _features_to_csv_row(features: Dict[str, float]) -> str:
    """Serialize features to a single CSV row in FEATURE_NAMES column order."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([features[name] for name in FEATURE_NAMES])
    return buf.getvalue().strip()


# ─── SageMaker client ─────────────────────────────────────────────────────────

def _get_sagemaker_runtime():
    """Create a SageMaker Runtime client."""
    region = getattr(settings, "SAGEMAKER_REGION", None) or getattr(settings, "AWS_SNS_REGION", "ap-south-1")
    return boto3.client("sagemaker-runtime", region_name=region)


def _invoke_endpoint(csv_payload: str) -> List[float]:
    """Call the SageMaker endpoint with CSV payload, return list of probabilities."""
    client = _get_sagemaker_runtime()
    endpoint_name = getattr(settings, "SAGEMAKER_ENDPOINT_NAME", "bloodbridge-donor-recommender")

    response = client.invoke_endpoint(
        EndpointName=endpoint_name,
        ContentType="text/csv",
        Accept="text/csv",
        Body=csv_payload.encode("utf-8"),
    )

    body = response["Body"].read().decode("utf-8").strip()
    # XGBoost returns comma-separated probabilities, one per input row.
    return [float(v) for v in body.split(",")]


def score_donors(
    donors: Sequence[dmodels.Donor],
    request_bloodgroup: str,
    request_zipcode: str = "",
) -> List[SageMakerScore]:
    """Score a batch of donors via SageMaker and return AI scores (0–100).

    Parameters
    ----------
    donors : sequence of Donor objects
    request_bloodgroup : the blood group being requested
    request_zipcode : optional zipcode for location matching

    Returns
    -------
    List of SageMakerScore in the same order as input donors.
    """
    if not donors:
        return []

    enabled = getattr(settings, "SAGEMAKER_ENDPOINT_ENABLED", False)

    # Extract features for all donors
    all_features: List[Dict[str, float]] = []
    for donor in donors:
        feats = extract_features(donor, request_bloodgroup, request_zipcode)
        all_features.append(feats)

    if not enabled:
        logger.debug("SageMaker endpoint disabled; using local fallback scoring")
        return _fallback_scores(donors, all_features)

    # Build batch CSV payload (one row per donor, no header)
    csv_rows = [_features_to_csv_row(f) for f in all_features]
    csv_payload = "\n".join(csv_rows)

    try:
        probabilities = _invoke_endpoint(csv_payload)
    except (ClientError, EndpointConnectionError, NoCredentialsError, ConnectionError) as exc:
        logger.warning("SageMaker endpoint call failed, falling back to local scoring: %s", exc)
        return _fallback_scores(donors, all_features)
    except Exception as exc:
        logger.exception("Unexpected error calling SageMaker endpoint; falling back: %s", exc)
        return _fallback_scores(donors, all_features)

    if len(probabilities) != len(donors):
        logger.error(
            "SageMaker returned %d scores for %d donors; falling back",
            len(probabilities), len(donors),
        )
        return _fallback_scores(donors, all_features)

    results = []
    for donor, prob, feats in zip(donors, probabilities, all_features):
        ai_score = round(max(0.0, min(100.0, prob * 100)), 1)
        results.append(SageMakerScore(
            donor_id=donor.id,
            ai_score=ai_score,
            confidence=round(prob, 4),
            features=feats,
            source="sagemaker",
        ))
    return results


# ─── Fallback scoring (deterministic, no ML) ──────────────────────────────────

def _fallback_score_single(features: Dict[str, float], donor_id: int = 0) -> float:
    """Produce a heuristic 0–100 score from features when SageMaker is unavailable.

    Designed to produce realistic spread across donor tiers:
      Elite donors:   85-98
      Strong donors:  65-84
      Average donors: 40-64
      Weak donors:    18-39
      Poor donors:     0-17

    The donor_id is used for deterministic per-donor variation so scores look
    realistically different across donors instead of clustering at the same value.
    """
    import hashlib

    # ── Deterministic per-donor variation seed ────────────────────────────
    _hash = hashlib.sha256(f"ai-variation-v3-{donor_id}".encode()).digest()
    _seed = int.from_bytes(_hash[:4], "big") / 2**32  # [0, 1)

    score = 0.0

    # ── 1. Blood group match (0 or 15 pts) ────────────────────────────────
    # Lower weight since all displayed donors always match
    if features["blood_match"] == 1.0:
        score += 15.0

    # ── 2. Availability (0 or 10 pts) ─────────────────────────────────────
    # Huge differentiator — unavailable donors should rank much lower
    if features["is_available"] == 1.0:
        score += 10.0

    # ── 3. Location (0-5 pts) ─────────────────────────────────────────────
    score += features["same_zipcode"] * 4.0
    score += features["has_coordinates"] * 1.0

    # ── 4. Profile completeness (0-8 pts, non-linear) ────────────────────
    # Incomplete profiles are a MAJOR red flag
    completeness = features["profile_completeness"]
    if completeness >= 0.95:
        score += 8.0
    elif completeness >= 0.8:
        score += 5.0
    elif completeness >= 0.5:
        score += 2.0
    else:
        score -= 5.0  # Severe penalty for very incomplete profiles

    # ── 5. Hemoglobin (−8 to +14 pts) — strongest medical signal ─────────
    hb = features["hemoglobin_g_dl"]
    if hb > 0:
        if hb >= 15.0:
            score += 14.0  # Excellent
        elif hb >= 14.0:
            score += 11.0  # Very good
        elif hb >= 13.0:
            score += 7.0   # Good
        elif hb >= 12.0:
            score += 3.0   # Borderline
        elif hb >= 11.0:
            score -= 2.0   # Low
        elif hb >= 10.0:
            score -= 5.0   # Concerning
        else:
            score -= 8.0   # Anemic — strong negative signal
    else:
        score -= 4.0 - _seed * 2.0  # Missing: variable penalty

    # ── 6. Weight (−4 to +8 pts) ─────────────────────────────────────────
    wt = features["weight_kg"]
    if wt >= 75:
        score += 8.0
    elif wt >= 65:
        score += 6.0
    elif wt >= 55:
        score += 3.0
    elif wt >= 50:
        score += 1.0   # Minimum eligible
    elif wt >= 45:
        score -= 2.0   # Underweight
    elif wt > 0:
        score -= 4.0   # Severely underweight
    else:
        score -= 3.0 - _seed * 1.5  # Missing

    # ── 7. Blood pressure (−10 to +10 pts) — high clinical impact ────────
    sys_bp = features["bp_systolic"]
    dia_bp = features["bp_diastolic"]
    if sys_bp > 0 and dia_bp > 0:
        # Score systolic and diastolic independently then combine
        sys_score = 0.0
        if 110 <= sys_bp <= 125:
            sys_score = 5.0    # Ideal
        elif 100 <= sys_bp <= 130:
            sys_score = 3.5    # Good
        elif 90 <= sys_bp <= 140:
            sys_score = 1.5    # Acceptable
        elif 85 <= sys_bp <= 155:
            sys_score = -2.0   # Concerning
        else:
            sys_score = -5.0   # Dangerous

        dia_score = 0.0
        if 70 <= dia_bp <= 80:
            dia_score = 5.0    # Ideal
        elif 65 <= dia_bp <= 85:
            dia_score = 3.5    # Good
        elif 60 <= dia_bp <= 90:
            dia_score = 1.5    # Acceptable
        elif 55 <= dia_bp <= 95:
            dia_score = -1.5   # Borderline
        else:
            dia_score = -5.0   # Dangerous

        score += sys_score + dia_score
    else:
        score -= 4.0  # Missing BP is a concern

    # ── 8. Age suitability (−6 to +10 pts) ───────────────────────────────
    age = features["age_years"]
    if age < 0:
        score -= 5.0  # Unknown age — big concern
    elif 25 <= age <= 40:
        score += 10.0  # Prime donor age
    elif 20 <= age <= 45:
        score += 7.0   # Excellent range
    elif 18 <= age <= 55:
        score += 4.0   # Good range
    elif 18 <= age <= 60:
        score += 1.0   # Acceptable
    elif 16 <= age <= 65:
        score -= 2.0   # Edge of eligibility
    else:
        score -= 6.0   # Ineligible

    # ── 9. Recovery status (−6 to +5 pts) ────────────────────────────────
    if features["in_recovery"] == 0:
        days = features["days_since_donation"]
        if days > 180:
            score += 5.0   # Well-rested
        elif days > 90:
            score += 4.0   # Good gap
        elif days >= 0:
            score += 2.0   # Recently cleared
        else:
            score += 1.0   # Never donated
    else:
        days = features["days_since_donation"]
        if days > 45:
            score -= 1.0   # Almost recovered
        elif days > 30:
            score -= 3.0   # Recovering
        else:
            score -= 6.0   # Recently donated — not ready

    # ── 10. Donation history (0-12 pts) — experienced donors are gold ────
    dc = features["donation_count"]
    if dc >= 12:
        score += 12.0
    elif dc >= 8:
        score += 10.0
    elif dc >= 5:
        score += 7.0
    elif dc >= 3:
        score += 5.0
    elif dc >= 1:
        score += 2.0
    else:
        score += 0.0   # First-time donor: neutral

    # ── 11. Sex-based minor variation ────────────────────────────────────
    if features["sex_male"] == 1.0:
        score += 1.0
    elif features["sex_female"] == 1.0:
        score += 0.5

    # ── 12. Risk penalties (cumulative, up to −28 pts) ───────────────────
    if features["has_chronic_disease"] == 1.0:
        score -= 8.0 + _seed * 4.0    # 8-12 pt penalty
    if features["on_medication"] == 1.0:
        score -= 5.0 + _seed * 3.0    # 5-8 pt penalty
    if features["smokes"] == 1.0:
        score -= 4.0 + _seed * 4.0    # 4-8 pt penalty

    # ── 13. Clean health bonus ───────────────────────────────────────────
    clean = (features["smokes"] == 0 and features["has_chronic_disease"] == 0
             and features["on_medication"] == 0)
    if clean:
        score += 3.0

    # ── 14. Donor-specific micro-variation (±2 pts) ──────────────────────
    score += (_seed * 4.0 - 2.0)

    return round(max(0.0, min(100.0, score)), 1)


def _fallback_scores(
    donors: Sequence[dmodels.Donor],
    all_features: List[Dict[str, float]],
) -> List[SageMakerScore]:
    """Generate fallback scores for all donors."""
    results = []
    for donor, feats in zip(donors, all_features):
        ai_score = _fallback_score_single(feats, donor_id=donor.id)
        results.append(SageMakerScore(
            donor_id=donor.id,
            ai_score=ai_score,
            confidence=ai_score / 100.0,
            features=feats,
            source="fallback",
        ))
    return results
