from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("blood", "0008_remove_matchalert"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="bloodrequest",
            constraint=models.CheckConstraint(
                check=~(models.Q(("patient__isnull", False)) & models.Q(("request_by_donor__isnull", False))),
                name="bloodrequest_not_both_patient_and_donor",
            ),
        ),
    ]
