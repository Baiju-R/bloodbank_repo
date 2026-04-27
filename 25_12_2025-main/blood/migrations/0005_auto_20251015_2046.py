# Legacy migration noop: the field is already named `patient` in 0001_initial.
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('blood', '0004_bloodrequest_date'),
    ]

    operations = []
