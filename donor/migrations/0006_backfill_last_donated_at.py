from django.db import migrations


def backfill_last_donated_at(apps, schema_editor):
    Donor = apps.get_model("donor", "Donor")
    BloodDonate = apps.get_model("donor", "BloodDonate")

    # For each donor, set last_donated_at to the most recent approved donation date.
    # Kept simple and safe for sqlite; uses per-donor lookup to avoid DB-specific subqueries.
    for donor in Donor.objects.all().only("id", "last_donated_at"):
        latest = (
            BloodDonate.objects.filter(donor_id=donor.id, status="Approved")
            .order_by("-date", "-id")
            .values_list("date", flat=True)
            .first()
        )
        if latest and donor.last_donated_at != latest:
            donor.last_donated_at = latest
            donor.save(update_fields=["last_donated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("donor", "0005_donor_medical_fields_last_donated_at"),
    ]

    operations = [
        migrations.RunPython(backfill_last_donated_at, migrations.RunPython.noop),
    ]
