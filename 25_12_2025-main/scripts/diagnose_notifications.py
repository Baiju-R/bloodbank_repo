import os
import sys
from pathlib import Path


def main() -> int:
    project_root = str(Path(__file__).resolve().parents[1])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bloodbankmanagement.settings")

    import django

    django.setup()

    from django.conf import settings

    from blood.models import InAppNotification
    from blood.models import BloodRequest
    from blood.services.donor_recommender import recommend_donors_for_request
    from blood.utils.phone import normalize_phone_number
    from blood.utils.sms_sender import check_sms_provider_health
    from donor.models import Donor
    from patient.models import Patient

    patient_raw = "9385425650"
    donor_raw = "9361046558"

    print("=== Phone normalization ===")
    print("patient_raw:", patient_raw, "->", normalize_phone_number(patient_raw))
    print("donor_raw  :", donor_raw, "->", normalize_phone_number(donor_raw))

    print("\n=== SMS config (from settings/env) ===")
    print("AWS_SNS_ENABLED:", getattr(settings, "AWS_SNS_ENABLED", None))
    print("AWS_SNS_REGION:", getattr(settings, "AWS_SNS_REGION", None))
    print("AWS_SNS_DEFAULT_COUNTRY_CODE:", getattr(settings, "AWS_SNS_DEFAULT_COUNTRY_CODE", None))

    print("\n=== SMS provider health ===")
    health = check_sms_provider_health()
    for key in sorted(health.keys()):
        print(f"{key}: {health[key]}")

    print("\n=== DB lookup: patient/donor by mobile ===")
    patient = (
        Patient.objects.filter(mobile__icontains=patient_raw)
        .select_related("user")
        .order_by("id")
        .first()
    )
    donor = (
        Donor.objects.filter(mobile__icontains=donor_raw)
        .select_related("user")
        .order_by("id")
        .first()
    )

    print(
        "patient_found:",
        bool(patient),
        "id:",
        getattr(patient, "id", None),
        "user:",
        getattr(getattr(patient, "user", None), "username", None),
    )
    print(
        "donor_found  :",
        bool(donor),
        "id:",
        getattr(donor, "id", None),
        "user:",
        getattr(getattr(donor, "user", None), "username", None),
    )

    print("\n=== Recent in-app notifications for these users ===")
    if patient:
        qs = InAppNotification.objects.filter(patient=patient).order_by("-created_at")[:5]
        print("patient_notifications_count_shown:", qs.count())
        for note in qs:
            title = (note.title or "").strip() or "Notification"
            msg = (note.message or "").strip().replace("\n", " ")
            print("-", note.created_at, "|", title, "|", msg[:140])
    else:
        print("patient_notifications_count_shown: n/a (patient not found)")

    if donor:
        qs = InAppNotification.objects.filter(donor=donor).order_by("-created_at")[:5]
        print("donor_notifications_count_shown:", qs.count())
        for note in qs:
            title = (note.title or "").strip() or "Notification"
            msg = (note.message or "").strip().replace("\n", " ")
            print("-", note.created_at, "|", title, "|", msg[:140])
    else:
        print("donor_notifications_count_shown: n/a (donor not found)")

    print("\n=== Latest patient requests + top recommended donor (if any) ===")
    if patient:
        recent_reqs = (
            BloodRequest.objects.filter(patient=patient)
            .order_by("-date", "-id")
            .only("id", "date", "status", "bloodgroup", "unit", "is_urgent")[:5]
        )
        if not recent_reqs:
            print("No blood requests found for this patient.")
        for req in recent_reqs:
            print(f"request_id={req.id} status={req.status} blood={req.bloodgroup} unit={req.unit} urgent={req.is_urgent} date={req.date}")
            if req.status != "Approved":
                continue
            try:
                recs = recommend_donors_for_request(req, limit=1, require_eligible=True)
            except Exception as exc:
                print("  recommender_error:", exc)
                continue
            top = recs[0] if recs else None
            if not top:
                print("  top_match: none")
                continue
            top_donor = getattr(top, "donor", None)
            top_phone = normalize_phone_number(getattr(top_donor, "mobile", None)) if top_donor else None
            print(
                "  top_match:",
                f"donor_id={getattr(top_donor, 'id', None)}",
                f"name={getattr(top_donor, 'get_name', None)}",
                f"blood={getattr(top_donor, 'bloodgroup', None)}",
                f"phone={top_phone}",
                f"score={getattr(top, 'score', None)}",
            )
            if donor and top_donor and donor.id == top_donor.id:
                print("  note: provided donor number IS the top match")
            elif donor:
                print("  note: provided donor number is NOT the top match")
    else:
        print("n/a (patient not found)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
