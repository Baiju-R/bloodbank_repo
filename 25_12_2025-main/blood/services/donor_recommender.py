from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from math import radians, sin, cos, sqrt, atan2
from typing import List, Optional, Sequence, Tuple

from django.conf import settings
from django.utils import timezone

from donor import models as dmodels
from blood import models as bmodels


def _stable_jitter(*parts: object, amplitude: float = 0.75) -> float:
    """Small deterministic noise to break score ties.

    This avoids UI confusion when many donors have identical (or missing) medical fields.
    The jitter is stable across runs for the same input parts.
    """

    base = "|".join(str(p) for p in parts)
    # Use built-in hash (salted per-process) would be unstable; use a deterministic hash instead.
    import hashlib

    digest = hashlib.sha256(base.encode("utf-8")).digest()
    # Map first 4 bytes to [0, 1)
    bucket = int.from_bytes(digest[:4], "big") / 2**32
    return (bucket * 2.0 - 1.0) * float(amplitude)


@dataclass(frozen=True)
class DonorRecommendation:
    donor: dmodels.Donor
    score: float
    eligible: bool
    next_eligible_date: Optional[date]
    distance_km: Optional[float]
    reasons: Tuple[str, ...]
    blockers: Tuple[str, ...]
    ai_score: Optional[float] = None       # 0-100 SageMaker AI recommendation score
    ai_source: str = "none"                # "sagemaker", "fallback", or "none"


def _haversine_km(lat1: Decimal, lon1: Decimal, lat2: Decimal, lon2: Decimal) -> float:
    # Earth radius in KM
    R = 6371.0
    phi1, phi2 = radians(float(lat1)), radians(float(lat2))
    dphi = radians(float(lat2) - float(lat1))
    dlambda = radians(float(lon2) - float(lon1))
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return float(R * c)


def _get_recovery_days() -> int:
    return int(getattr(settings, "DONATION_RECOVERY_DAYS", 56))


def _get_weights() -> dict:
    # Tunable weights redesigned for wide score spread (0-100 effective range).
    # blood_match is de-emphasized since only matching donors are shown;
    # medical fitness and donation history get much higher impact.
    default = {
        "blood_match": 20.0,
        "available": 12.0,
        "same_zip": 6.0,
        "has_coords": 1.0,
        "distance_km_penalty": 0.15,  # points per km
        "missing_medical_penalty": 5.0,
        "hemoglobin_excellent": 10.0,
        "hemoglobin_good": 6.0,
        "hemoglobin_borderline": 2.0,
        "hemoglobin_low_penalty": 4.0,
        "bp_ideal": 8.0,
        "bp_good": 5.0,
        "bp_acceptable": 2.0,
        "bp_poor_penalty": 5.0,
        "weight_excellent": 7.0,
        "weight_good": 4.0,
        "weight_borderline": 1.0,
        "weight_low_penalty": 3.0,
        "age_prime": 8.0,
        "age_good": 5.0,
        "age_acceptable": 2.0,
        "age_edge_penalty": 3.0,
        "chronic_penalty": 10.0,
        "medication_penalty": 7.0,
        "smokes_penalty": 5.0,
        "clean_health_bonus": 4.0,
        "donation_veteran": 12.0,    # 10+ donations
        "donation_experienced": 8.0,  # 5-9
        "donation_moderate": 5.0,     # 3-4
        "donation_some": 2.0,         # 1-2
        "recovery_clear": 4.0,
        "recovery_penalty": 6.0,
    }
    configured = getattr(settings, "SMART_DONOR_MODEL_WEIGHTS", None)
    if isinstance(configured, dict):
        default.update({k: float(v) for k, v in configured.items() if v is not None})
    return default


def _age_from_dob(dob: Optional[date]) -> Optional[int]:
    if not dob:
        return None
    today = timezone.now().date()
    years = today.year - dob.year
    if (today.month, today.day) < (dob.month, dob.day):
        years -= 1
    return years


def _hemoglobin_threshold(sex: str) -> float:
    # Conservative defaults; can be adjusted in settings if needed.
    # Fallback to 12.5 for unknown.
    if sex == "M":
        return float(getattr(settings, "DONOR_HB_MIN_MALE", 13.0))
    if sex == "F":
        return float(getattr(settings, "DONOR_HB_MIN_FEMALE", 12.5))
    return float(getattr(settings, "DONOR_HB_MIN_UNKNOWN", 12.5))


def _is_bp_ok(sys_val: Optional[int], dia_val: Optional[int]) -> Optional[bool]:
    if sys_val is None or dia_val is None:
        return None
    # Basic broad ranges.
    return 90 <= sys_val <= 180 and 50 <= dia_val <= 110


def recommend_donors_for_request(
    blood_request: bmodels.BloodRequest,
    *,
    limit: int = 10,
    require_eligible: bool = True,
) -> List[DonorRecommendation]:
    """Recommend donors for an admin to contact before approving a request.

    The scoring is deterministic but weight-based ("ML-like") so it can evolve
    without changing the UI or DB schema.
    """

    weights = _get_weights()
    today = timezone.now().date()
    recovery_days = _get_recovery_days()

    candidates: Sequence[dmodels.Donor] = (
        dmodels.Donor.objects.select_related("user")
        .filter(bloodgroup=blood_request.bloodgroup)
        .order_by("id")
    )

    recs: List[DonorRecommendation] = []

    for donor in candidates:
        score = 0.0
        reasons: List[str] = []
        blockers: List[str] = []

        # Exact group match (already filtered)
        score += weights["blood_match"]
        reasons.append(f"Blood group match: {donor.bloodgroup}")

        # Medical report validity check
        if not donor.is_medical_report_valid:
            blockers.append("Medical health report is expired or missing")
            score -= 30  # Heavy penalty
            reasons.append("No valid medical report (expired or not uploaded)")
        else:
            days_left = donor.medical_report_days_remaining
            if days_left is not None:
                reasons.append(f"Medical report valid ({days_left} days remaining)")
                if days_left <= 14:
                    score -= 5  # Minor penalty for expiring soon

        # Availability
        if donor.is_available:
            score += weights["available"]
            reasons.append("Marked available")
        else:
            blockers.append("Donor is marked unavailable")

        # Recovery eligibility
        next_eligible = donor.next_eligible_donation_date
        if donor.last_donated_at:
            days_since = (today - donor.last_donated_at).days
            reasons.append(f"Last donated: {donor.last_donated_at}")
            if next_eligible:
                reasons.append(f"Next eligible: {next_eligible} (recovery {recovery_days} days)")
            if next_eligible and today < next_eligible:
                blockers.append(f"Still in recovery window until {next_eligible}")
                score -= weights["recovery_penalty"]
            else:
                score += weights["recovery_clear"]
        else:
            reasons.append("No recorded donations")
            score += weights["recovery_clear"] * 0.5  # partial credit for never-donated

        # Age eligibility (if available)
        age_years = _age_from_dob(donor.date_of_birth)
        if age_years is None:
            score -= weights["missing_medical_penalty"]
            reasons.append("Age not provided (DOB missing)")
        else:
            reasons.append(f"Age: {age_years}")
            if age_years < 18 or age_years > 65:
                blockers.append("Age outside 18–65 eligibility range")
                score -= weights["age_edge_penalty"]
            elif 25 <= age_years <= 40:
                score += weights["age_prime"]
                reasons.append("Prime donor age range")
            elif 20 <= age_years <= 50:
                score += weights["age_good"]
            else:
                score += weights["age_acceptable"]

        # Weight eligibility (if available)
        if donor.weight_kg is None:
            score -= weights["missing_medical_penalty"]
            reasons.append("Weight not provided")
        else:
            reasons.append(f"Weight: {donor.weight_kg} kg")
            min_wt = int(getattr(settings, "DONOR_WEIGHT_MIN_KG", 50))
            if donor.weight_kg < min_wt:
                blockers.append("Weight below minimum eligibility")
                score -= weights["weight_low_penalty"]
            elif donor.weight_kg >= 65:
                score += weights["weight_excellent"]
            elif donor.weight_kg >= 55:
                score += weights["weight_good"]
            else:
                score += weights["weight_borderline"]

        # Hemoglobin eligibility (if available)
        if donor.hemoglobin_g_dl is None:
            score -= weights["missing_medical_penalty"]
            reasons.append("Hemoglobin not provided")
        else:
            hb = float(donor.hemoglobin_g_dl)
            threshold = _hemoglobin_threshold(donor.sex)
            reasons.append(f"Hemoglobin: {hb:.1f} g/dL")
            if hb < threshold:
                blockers.append(f"Hemoglobin below threshold ({threshold:.1f} g/dL)")
                score -= weights["hemoglobin_low_penalty"]
            elif hb >= 15.0:
                score += weights["hemoglobin_excellent"]
                reasons.append("Excellent hemoglobin level")
            elif hb >= 13.5:
                score += weights["hemoglobin_good"]
            else:
                score += weights["hemoglobin_borderline"]

        # Blood pressure (graded, not just ok/not-ok)
        if donor.blood_pressure_systolic is None or donor.blood_pressure_diastolic is None:
            score -= weights["missing_medical_penalty"] * 0.5
            reasons.append("Blood pressure not provided")
        else:
            sys_val = donor.blood_pressure_systolic
            dia_val = donor.blood_pressure_diastolic
            reasons.append(f"Blood pressure: {sys_val}/{dia_val}")
            # Graded BP scoring
            sys_ok = 90 <= sys_val <= 140
            dia_ok = 60 <= dia_val <= 90
            sys_ideal = 110 <= sys_val <= 125
            dia_ideal = 70 <= dia_val <= 80
            if sys_ideal and dia_ideal:
                score += weights["bp_ideal"]
                reasons.append("Ideal blood pressure")
            elif sys_ok and dia_ok:
                score += weights["bp_good"]
            elif 85 <= sys_val <= 155 and 55 <= dia_val <= 95:
                score += weights["bp_acceptable"]
            else:
                blockers.append("Blood pressure outside safe range")
                score -= weights["bp_poor_penalty"]

        # Donation history bonus (graduated)
        from donor.models import BloodDonate
        donation_count = BloodDonate.objects.filter(
            donor=donor, status="Approved"
        ).count()
        if donation_count >= 10:
            score += weights["donation_veteran"]
            reasons.append(f"Veteran donor ({donation_count} donations)")
        elif donation_count >= 5:
            score += weights["donation_experienced"]
            reasons.append(f"Experienced donor ({donation_count} donations)")
        elif donation_count >= 3:
            score += weights["donation_moderate"]
            reasons.append(f"Moderate experience ({donation_count} donations)")
        elif donation_count >= 1:
            score += weights["donation_some"]
            reasons.append(f"Some experience ({donation_count} donation{'s' if donation_count > 1 else ''})")
        else:
            reasons.append("First-time donor")

        # Risk flags (not hard blockers by default)
        if donor.has_chronic_disease:
            score -= weights["chronic_penalty"]
            details = donor.chronic_disease_details.strip()
            reasons.append(f"Chronic condition reported{' (' + details + ')' if details else ''}")
        if donor.on_medication:
            score -= weights["medication_penalty"]
            details = donor.medication_details.strip()
            reasons.append(f"Medication reported{' (' + details + ')' if details else ''}")
        if donor.smokes:
            score -= weights["smokes_penalty"]
            reasons.append("Smoking reported")

        # Clean health bonus — no risk factors at all
        if not donor.has_chronic_disease and not donor.on_medication and not donor.smokes:
            score += weights["clean_health_bonus"]
            reasons.append("Clean health profile")

        # Location closeness
        distance_km: Optional[float] = None
        if blood_request.request_zipcode and donor.zipcode:
            if blood_request.request_zipcode.strip() == donor.zipcode.strip():
                score += weights["same_zip"]
                reasons.append("Same zipcode as request")
        if donor.latitude is not None and donor.longitude is not None:
            score += weights["has_coords"]
            # We can only compute distance if request has coordinates; currently requests only store zipcode.
            # If future versions add request coordinates, distance will be computed.

        # If both donor coords exist and request zip can be geocoded via fixtures/remote, compute distance.
        if donor.latitude is not None and donor.longitude is not None and blood_request.request_zipcode:
            from blood.services.geocoding import geocode_address

            req_geo = geocode_address(blood_request.request_zipcode, allow_remote=False)
            if req_geo is not None:
                distance_km = _haversine_km(donor.latitude, donor.longitude, req_geo.latitude, req_geo.longitude)
                score -= float(distance_km) * weights["distance_km_penalty"]
                reasons.append(f"Approx distance: {distance_km:.1f} km")

        eligible = len(blockers) == 0
        if require_eligible and not eligible:
            continue

        # Tie-breaker to prevent identical-looking scores when many fields are missing.
        score += _stable_jitter("donor", donor.id, "request", blood_request.id)

        # Clamp score to 0–100 range
        score = max(0.0, min(100.0, score))

        recs.append(
            DonorRecommendation(
                donor=donor,
                score=score,
                eligible=eligible,
                next_eligible_date=next_eligible,
                distance_km=distance_km,
                reasons=tuple(reasons),
                blockers=tuple(blockers),
            )
        )

    # ── Enrich with SageMaker AI scores ──────────────────────────────────
    if recs:
        try:
            from blood.services.sagemaker_scorer import score_donors

            rec_donors = [r.donor for r in recs]
            ai_results = score_donors(
                rec_donors,
                blood_request.bloodgroup,
                blood_request.request_zipcode or "",
            )
            # Merge AI scores into recommendations (frozen dataclass → rebuild)
            ai_map = {s.donor_id: s for s in ai_results}
            enriched: List[DonorRecommendation] = []
            for rec in recs:
                sm = ai_map.get(rec.donor.id)
                if sm:
                    enriched.append(
                        DonorRecommendation(
                            donor=rec.donor,
                            score=rec.score,
                            eligible=rec.eligible,
                            next_eligible_date=rec.next_eligible_date,
                            distance_km=rec.distance_km,
                            reasons=rec.reasons,
                            blockers=rec.blockers,
                            ai_score=sm.ai_score,
                            ai_source=sm.source,
                        )
                    )
                else:
                    enriched.append(rec)
            recs = enriched
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "SageMaker enrichment failed; AI scores omitted: %s", exc
            )

    # Always prefer available donors first, then highest score.
    recs.sort(key=lambda r: (bool(getattr(r.donor, "is_available", False)), r.score), reverse=True)
    return recs[: max(1, int(limit))]
