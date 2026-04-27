from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("blood", "0007_auto_20251130_XXXX"),
    ]

    operations = [
        migrations.DeleteModel(
            name="MatchAlert",
        ),
    ]
