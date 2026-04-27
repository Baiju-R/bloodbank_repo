# Legacy migration noop: the modern schema ships in 0001_initial.
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('blood', '0001_initial'),
    ]

    operations = []
