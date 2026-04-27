from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('donor', '0006_backfill_last_donated_at'),
        ('patient', '0001_initial'),
        ('blood', '0015_reportexportlog'),
    ]

    operations = [
        migrations.CreateModel(
            name='DonationAppointmentSlot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('start_at', models.DateTimeField()),
                ('end_at', models.DateTimeField()),
                ('capacity', models.PositiveIntegerField(default=10)),
                ('is_active', models.BooleanField(default=True)),
                ('notes', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['start_at', 'id'],
            },
        ),
        migrations.CreateModel(
            name='EmergencyBroadcast',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('message', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('PENDING', 'Pending'), ('SENT', 'Sent'), ('PARTIAL', 'Partially Sent'), ('FAILED', 'Failed')], default='PENDING', max_length=16)),
                ('total_targets', models.PositiveIntegerField(default=0)),
                ('total_sent', models.PositiveIntegerField(default=0)),
                ('total_failed', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('blood_request', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='broadcasts', to='blood.bloodrequest')),
                ('triggered_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='InAppNotification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=120)),
                ('message', models.TextField()),
                ('is_read', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('donor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='donor.donor')),
                ('patient', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='patient.patient')),
                ('related_request', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='blood.bloodrequest')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='VerificationBadge',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('badge_name', models.CharField(default='Verified Identity', max_length=60)),
                ('hospital_name', models.CharField(blank=True, max_length=120)),
                ('is_verified', models.BooleanField(default=False)),
                ('trust_score', models.PositiveSmallIntegerField(default=50)),
                ('verified_at', models.DateTimeField(blank=True, null=True)),
                ('notes', models.CharField(blank=True, max_length=255)),
                ('donor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='donor.donor')),
                ('patient', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='patient.patient')),
                ('verified_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-verified_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='DonationAppointment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('requested_for', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(choices=[('PENDING', 'Pending'), ('APPROVED', 'Approved'), ('RESCHEDULED', 'Rescheduled'), ('NO_SHOW', 'No Show'), ('COMPLETED', 'Completed'), ('CANCELLED', 'Cancelled')], default='PENDING', max_length=16)),
                ('notes', models.CharField(blank=True, max_length=255)),
                ('reminder_sent_at', models.DateTimeField(blank=True, null=True)),
                ('requested_at', models.DateTimeField(auto_now_add=True)),
                ('donor', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='donor.donor')),
                ('slot', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='blood.donationappointmentslot')),
            ],
            options={
                'ordering': ['-requested_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='BroadcastDelivery',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('channel', models.CharField(choices=[('SMS', 'SMS'), ('EMAIL', 'Email'), ('INAPP', 'In-App')], max_length=12)),
                ('status', models.CharField(choices=[('PENDING', 'Pending'), ('SENT', 'Sent'), ('FAILED', 'Failed')], default='PENDING', max_length=12)),
                ('destination', models.CharField(blank=True, max_length=160)),
                ('detail', models.CharField(blank=True, max_length=255)),
                ('delivered_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('broadcast', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='deliveries', to='blood.emergencybroadcast')),
                ('donor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='donor.donor')),
            ],
            options={
                'ordering': ['-id'],
            },
        ),
    ]
