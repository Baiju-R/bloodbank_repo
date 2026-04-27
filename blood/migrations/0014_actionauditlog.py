from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('blood', '0013_feedback_is_seeded_demo'),
    ]

    operations = [
        migrations.CreateModel(
            name='ActionAuditLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(choices=[('APPROVE_REQUEST', 'Approve Request'), ('REJECT_REQUEST', 'Reject Request'), ('APPROVE_DONATION', 'Approve Donation'), ('REJECT_DONATION', 'Reject Donation')], max_length=32)),
                ('entity_type', models.CharField(choices=[('REQUEST', 'Blood Request'), ('DONATION', 'Blood Donation')], max_length=16)),
                ('entity_id', models.PositiveIntegerField(db_index=True)),
                ('bloodgroup', models.CharField(blank=True, max_length=10)),
                ('units', models.PositiveIntegerField(default=0)),
                ('status_before', models.CharField(blank=True, max_length=20)),
                ('status_after', models.CharField(blank=True, max_length=20)),
                ('actor_role', models.CharField(blank=True, max_length=80)),
                ('actor_username', models.CharField(blank=True, max_length=150)),
                ('notes', models.CharField(blank=True, max_length=255)),
                ('payload', models.JSONField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Action Audit Log',
                'verbose_name_plural': 'Action Audit Logs',
                'ordering': ['-created_at', '-id'],
                'permissions': [('can_review_requests', 'Can review and action blood requests'), ('can_review_donations', 'Can review and action blood donations'), ('can_view_audit_logs', 'Can view action audit logs'), ('can_export_reports', 'Can export admin reports')],
            },
        ),
    ]
