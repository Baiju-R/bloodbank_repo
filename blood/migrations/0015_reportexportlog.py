from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('blood', '0014_actionauditlog'),
    ]

    operations = [
        migrations.CreateModel(
            name='ReportExportLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('report_key', models.CharField(max_length=32)),
                ('export_format', models.CharField(choices=[('csv', 'CSV'), ('pdf', 'PDF')], max_length=8)),
                ('rows_exported', models.PositiveIntegerField(default=0)),
                ('actor_username', models.CharField(blank=True, max_length=150)),
                ('actor_role', models.CharField(blank=True, max_length=80)),
                ('filters', models.JSONField(blank=True, null=True)),
                ('status', models.CharField(choices=[('SUCCESS', 'Success'), ('FALLBACK', 'Fallback'), ('FAILED', 'Failed')], default='SUCCESS', max_length=16)),
                ('error', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Report Export Log',
                'verbose_name_plural': 'Report Export Logs',
                'ordering': ['-created_at', '-id'],
            },
        ),
    ]
