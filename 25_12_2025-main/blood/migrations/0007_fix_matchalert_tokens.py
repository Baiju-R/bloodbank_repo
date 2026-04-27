from __future__ import annotations

import uuid

from django.db import migrations, models

import blood.models


def regenerate_confirmation_tokens(apps, schema_editor):
    MatchAlert = apps.get_model("blood", "MatchAlert")

    assigned_tokens: set[str] = set()
    for alert in MatchAlert.objects.all().order_by("id"):
        token = alert.confirmation_token
        if not token or token in assigned_tokens:
            token = uuid.uuid4().hex
            while token in assigned_tokens:
                token = uuid.uuid4().hex
            alert.confirmation_token = token
            alert.save(update_fields=["confirmation_token"])
        assigned_tokens.add(token)


class Migration(migrations.Migration):

    dependencies = [
        ("blood", "0006_bloodrequest_is_urgent_bloodrequest_request_zipcode_and_more"),
    ]

    operations = [
        migrations.RunPython(regenerate_confirmation_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="matchalert",
            name="confirmation_token",
            field=models.CharField(
                default=blood.models.generate_confirmation_token,
                max_length=64,
                unique=True,
            ),
        ),
    ]
