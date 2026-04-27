from django.db import migrations, models


def backfill_seeded_demo(apps, schema_editor):
    Feedback = apps.get_model("blood", "Feedback")

    # Historic seeded anonymous feedback used display_name like: "Demo Feedback 1".
    Feedback.objects.filter(
        author_type="ANONYMOUS",
        display_name__startswith="Demo Feedback ",
    ).update(is_seeded_demo=True)


class Migration(migrations.Migration):

    dependencies = [
        ("blood", "0012_feedback"),
    ]

    operations = [
        migrations.AddField(
            model_name="feedback",
            name="is_seeded_demo",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.RunPython(backfill_seeded_demo, migrations.RunPython.noop),
    ]
