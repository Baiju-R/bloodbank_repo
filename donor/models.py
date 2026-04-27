from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator, RegexValidator
from django.utils import timezone
from django.conf import settings
from datetime import timedelta

AADHAAR_VALIDATOR = RegexValidator(
    regex=r'^\d{12}$',
    message='Aadhaar number must be exactly 12 digits.',
)

MEDICAL_REPORT_VALIDITY_DAYS = 90  # 3 months


class Donor(models.Model):
    user=models.OneToOneField(User,on_delete=models.CASCADE)
    profile_pic= models.ImageField(upload_to='profile_pic/Donor/',null=True,blank=True)

    # Aadhaar number (mandatory)
    aadhaar_number = models.CharField(
        max_length=12,
        validators=[AADHAAR_VALIDATOR],
        help_text='12-digit Aadhaar number',
        default='',
    )

    # Admin approval workflow
    is_approved = models.BooleanField(default=False, help_text='Admin must approve before account is active')
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=255, blank=True)

    bloodgroup=models.CharField(max_length=10)
    address = models.CharField(max_length=255)
    mobile = models.CharField(max_length=20,null=False)
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
        help_text="Decimal latitude between -90 and 90"
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
        help_text="Decimal longitude between -180 and 180"
    )
    location_verified = models.BooleanField(default=False)
    zipcode = models.CharField(max_length=12, blank=True)
    is_available = models.BooleanField(default=True)
    availability_updated_at = models.DateTimeField(null=True, blank=True)
    last_notified_at = models.DateTimeField(null=True, blank=True)

    # Medical + eligibility features (kept optional for backward compatibility)
    sex = models.CharField(
        max_length=1,
        choices=(('M', 'Male'), ('F', 'Female'), ('O', 'Other'), ('U', 'Prefer not to say')),
        default='U',
    )
    date_of_birth = models.DateField(null=True, blank=True)
    weight_kg = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(300)],
    )
    hemoglobin_g_dl = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(25)],
        help_text="Optional. Typical eligibility is ~12.5+ g/dL.",
    )
    blood_pressure_systolic = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(50), MaxValueValidator(250)],
    )
    blood_pressure_diastolic = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(30), MaxValueValidator(150)],
    )
    has_chronic_disease = models.BooleanField(default=False)
    chronic_disease_details = models.CharField(max_length=255, blank=True)
    on_medication = models.BooleanField(default=False)
    medication_details = models.CharField(max_length=255, blank=True)
    smokes = models.BooleanField(default=False)

    # Donation recovery tracking
    last_donated_at = models.DateField(null=True, blank=True)
   
    @property
    def get_name(self):
        return self.user.first_name+" "+self.user.last_name
    
    @property
    def get_instance(self):
        return self
    
    @property
    def has_profile_pic(self):
        return self.profile_pic and hasattr(self.profile_pic, 'url')
    
    def __str__(self):
        return self.user.first_name

    def mark_availability(self, available: bool):
        self.is_available = available
        self.availability_updated_at = timezone.now()
        self.save(update_fields=["is_available", "availability_updated_at"])

    @property
    def age_years(self):
        if not self.date_of_birth:
            return None
        today = timezone.now().date()
        years = today.year - self.date_of_birth.year
        if (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day):
            years -= 1
        return years

    @property
    def donation_recovery_days(self) -> int:
        return int(getattr(settings, "DONATION_RECOVERY_DAYS", 56))

    @property
    def next_eligible_donation_date(self):
        if not self.last_donated_at:
            return None
        return self.last_donated_at + timedelta(days=self.donation_recovery_days)

    @property
    def latest_medical_report(self):
        """Return the most recent medical report, or None."""
        return self.medicalreport_set.order_by('-uploaded_at').first()

    @property
    def is_medical_report_valid(self):
        """True if the donor has a medical report uploaded within the last 3 months."""
        report = self.latest_medical_report
        if not report:
            return False
        expiry = report.uploaded_at + timedelta(days=MEDICAL_REPORT_VALIDITY_DAYS)
        return timezone.now() < expiry

    @property
    def medical_report_expiry_date(self):
        """Return the expiry date of the latest medical report, or None."""
        report = self.latest_medical_report
        if not report:
            return None
        return (report.uploaded_at + timedelta(days=MEDICAL_REPORT_VALIDITY_DAYS)).date()

    @property
    def medical_report_days_remaining(self):
        """Days until current medical report expires. Negative = expired."""
        expiry = self.medical_report_expiry_date
        if expiry is None:
            return None
        return (expiry - timezone.now().date()).days


class MedicalReport(models.Model):
    """Stores medical health reports uploaded by donors. Must be renewed every 3 months."""
    donor = models.ForeignKey(Donor, on_delete=models.CASCADE)
    document = models.FileField(
        upload_to='medical_reports/donor/%Y/%m/',
        help_text='Upload medical health report (PDF, JPG, PNG, DOC, etc.)',
    )
    document_name = models.CharField(max_length=255, blank=True)
    notes = models.CharField(max_length=500, blank=True, help_text='Optional notes about this report')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_verified = models.BooleanField(default=False, help_text='Admin verified this report')
    verified_by = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='verified_donor_reports'
    )
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-uploaded_at']
        verbose_name = 'Medical Report'
        verbose_name_plural = 'Medical Reports'

    def __str__(self):
        return f"Medical Report for {self.donor.get_name} ({self.uploaded_at:%d %b %Y})"

    @property
    def is_expired(self):
        expiry = self.uploaded_at + timedelta(days=MEDICAL_REPORT_VALIDITY_DAYS)
        return timezone.now() > expiry

    @property
    def expiry_date(self):
        return (self.uploaded_at + timedelta(days=MEDICAL_REPORT_VALIDITY_DAYS)).date()


class BloodDonate(models.Model): 
    donor=models.ForeignKey(Donor,on_delete=models.CASCADE)   
    disease=models.CharField(max_length=100,default="Nothing")
    age=models.PositiveIntegerField()
    bloodgroup=models.CharField(max_length=10)
    unit=models.PositiveIntegerField(default=0)
    status=models.CharField(max_length=20,default="Pending")
    date=models.DateField(auto_now=True)
    
    def __str__(self):
        return f"{self.donor.get_name} - {self.bloodgroup} - {self.status}"
    
    class Meta:
        ordering = ['-date', '-id']  # Most recent first
        verbose_name = "Blood Donation"
        verbose_name_plural = "Blood Donations"