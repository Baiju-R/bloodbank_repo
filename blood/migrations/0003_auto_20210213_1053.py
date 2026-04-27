# Legacy migration noop: 0001_initial already reflects the renamed fields and relations.
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('patient', '0001_initial'),
        ('donor', '0001_initial'),
        ('blood', '0002_bloodrequest'),
    ]

    operations = []
