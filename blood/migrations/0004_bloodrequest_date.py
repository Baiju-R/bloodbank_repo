# Legacy migration noop: `date` now exists in the initial state.
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('blood', '0003_auto_20210213_1053'),
    ]

    operations = []
