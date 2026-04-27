from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ("donor", "0004_donor_availability_updated_at_donor_is_available_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="donor",
            name="sex",
            field=models.CharField(
                choices=[("M", "Male"), ("F", "Female"), ("O", "Other"), ("U", "Prefer not to say")],
                default="U",
                max_length=1,
            ),
        ),
        migrations.AddField(
            model_name="donor",
            name="date_of_birth",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="donor",
            name="weight_kg",
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(300),
                ],
            ),
        ),
        migrations.AddField(
            model_name="donor",
            name="hemoglobin_g_dl",
            field=models.DecimalField(
                blank=True,
                decimal_places=1,
                help_text="Optional. Typical eligibility is ~12.5+ g/dL.",
                max_digits=4,
                null=True,
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(25),
                ],
            ),
        ),
        migrations.AddField(
            model_name="donor",
            name="blood_pressure_systolic",
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                validators=[
                    django.core.validators.MinValueValidator(50),
                    django.core.validators.MaxValueValidator(250),
                ],
            ),
        ),
        migrations.AddField(
            model_name="donor",
            name="blood_pressure_diastolic",
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                validators=[
                    django.core.validators.MinValueValidator(30),
                    django.core.validators.MaxValueValidator(150),
                ],
            ),
        ),
        migrations.AddField(
            model_name="donor",
            name="has_chronic_disease",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="donor",
            name="chronic_disease_details",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="donor",
            name="on_medication",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="donor",
            name="medication_details",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="donor",
            name="smokes",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="donor",
            name="last_donated_at",
            field=models.DateField(blank=True, null=True),
        ),
    ]
