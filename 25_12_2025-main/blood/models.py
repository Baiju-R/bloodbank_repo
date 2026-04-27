from django.db import models
from django.contrib.auth.models import User

from patient import models as pmodels
from donor import models as dmodels


def generate_confirmation_token() -> str:
    """Legacy helper kept so historic migrations keep importing cleanly."""

    return "legacy-token"

class Stock(models.Model):
    bloodgroup=models.CharField(max_length=10)
    unit=models.PositiveIntegerField(default=0)
    def __str__(self):
        return self.bloodgroup

class BloodRequest(models.Model):
    patient=models.ForeignKey(pmodels.Patient,null=True,blank=True,on_delete=models.CASCADE)
    request_by_donor=models.ForeignKey(dmodels.Donor,null=True,blank=True,on_delete=models.CASCADE)
    patient_name=models.CharField(max_length=30)
    patient_age=models.PositiveIntegerField()
    reason=models.CharField(max_length=500)
    bloodgroup=models.CharField(max_length=10)
    unit=models.PositiveIntegerField(default=0)
    status=models.CharField(max_length=20,default="Pending")
    date=models.DateField(auto_now=True)
    is_urgent = models.BooleanField(default=False)
    request_zipcode = models.CharField(max_length=12, blank=True)

    # Doctor prescription (mandatory for every request including emergency)
    doctor_prescription = models.FileField(
        upload_to='prescriptions/requests/%Y/%m/',
        null=True,
        blank=True,
        help_text='Upload doctor prescription for this blood request',
    )

    # Admin SMS diagnostics (last approval notification attempt)
    sms_last_approval_attempt_at = models.DateTimeField(null=True, blank=True)
    sms_last_approval_patient_to = models.CharField(max_length=32, blank=True)
    sms_last_approval_donor_to = models.CharField(max_length=32, blank=True)
    sms_last_approval_result = models.JSONField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=~(models.Q(patient__isnull=False) & models.Q(request_by_donor__isnull=False)),
                name="bloodrequest_not_both_patient_and_donor",
            ),
        ]

    def __str__(self):
        return f"{self.patient_name} - {self.bloodgroup}"


class Feedback(models.Model):
    AUTHOR_DONOR = "DONOR"
    AUTHOR_PATIENT = "PATIENT"
    AUTHOR_ANONYMOUS = "ANONYMOUS"

    FEEDBACK_DONATION = "DONATION"
    FEEDBACK_REQUEST = "REQUEST"
    FEEDBACK_GENERAL = "GENERAL"

    ADMIN_REACTIONS = [
        ("", "—"),
        ("👍", "👍"),
        ("❤️", "❤️"),
        ("🙏", "🙏"),
        ("👏", "👏"),
        ("😊", "😊"),
        ("🎉", "🎉"),
    ]

    author_type = models.CharField(
        max_length=16,
        choices=[
            (AUTHOR_DONOR, "Donor"),
            (AUTHOR_PATIENT, "Patient"),
            (AUTHOR_ANONYMOUS, "Anonymous"),
        ],
        default=AUTHOR_ANONYMOUS,
    )
    donor = models.ForeignKey(dmodels.Donor, null=True, blank=True, on_delete=models.SET_NULL)
    patient = models.ForeignKey(pmodels.Patient, null=True, blank=True, on_delete=models.SET_NULL)

    display_name = models.CharField(max_length=60, blank=True)

    # Internal flag used by demo seeding commands so we can safely refresh/cleanup
    # seeded records without touching real user feedback.
    is_seeded_demo = models.BooleanField(default=False, db_index=True)

    feedback_for = models.CharField(
        max_length=16,
        choices=[
            (FEEDBACK_DONATION, "Donation"),
            (FEEDBACK_REQUEST, "Request"),
            (FEEDBACK_GENERAL, "General"),
        ],
        default=FEEDBACK_GENERAL,
    )
    rating = models.PositiveSmallIntegerField(default=5)
    message = models.TextField(max_length=1200)

    image1 = models.ImageField(upload_to="feedback/%Y/%m/", null=True, blank=True)
    image2 = models.ImageField(upload_to="feedback/%Y/%m/", null=True, blank=True)
    image3 = models.ImageField(upload_to="feedback/%Y/%m/", null=True, blank=True)

    is_public = models.BooleanField(default=True)

    admin_reaction = models.CharField(max_length=8, blank=True, choices=ADMIN_REACTIONS)
    admin_reply = models.TextField(max_length=1200, blank=True)
    admin_updated_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                check=~(models.Q(donor__isnull=False) & models.Q(patient__isnull=False)),
                name="feedback_not_both_donor_and_patient",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(author_type="ANONYMOUS", donor__isnull=True, patient__isnull=True)
                    | models.Q(author_type="DONOR", donor__isnull=False, patient__isnull=True)
                    | models.Q(author_type="PATIENT", donor__isnull=True, patient__isnull=False)
                ),
                name="feedback_author_matches_fk",
            ),
            models.CheckConstraint(
                check=models.Q(rating__gte=1) & models.Q(rating__lte=5),
                name="feedback_rating_1_to_5",
            ),
        ]

    @property
    def author_label(self) -> str:
        if self.author_type == self.AUTHOR_DONOR and self.donor_id:
            return self.donor.get_name
        if self.author_type == self.AUTHOR_PATIENT and self.patient_id:
            return self.patient.get_name
        return self.display_name.strip() or "Anonymous"

    def __str__(self):
        return f"{self.author_label} ({self.rating}★)"


class ActionAuditLog(models.Model):
    ACTION_APPROVE_REQUEST = "APPROVE_REQUEST"
    ACTION_REJECT_REQUEST = "REJECT_REQUEST"
    ACTION_APPROVE_DONATION = "APPROVE_DONATION"
    ACTION_REJECT_DONATION = "REJECT_DONATION"

    ENTITY_REQUEST = "REQUEST"
    ENTITY_DONATION = "DONATION"

    action = models.CharField(
        max_length=32,
        choices=[
            (ACTION_APPROVE_REQUEST, "Approve Request"),
            (ACTION_REJECT_REQUEST, "Reject Request"),
            (ACTION_APPROVE_DONATION, "Approve Donation"),
            (ACTION_REJECT_DONATION, "Reject Donation"),
        ],
    )
    entity_type = models.CharField(
        max_length=16,
        choices=[
            (ENTITY_REQUEST, "Blood Request"),
            (ENTITY_DONATION, "Blood Donation"),
        ],
    )
    entity_id = models.PositiveIntegerField(db_index=True)
    bloodgroup = models.CharField(max_length=10, blank=True)
    units = models.PositiveIntegerField(default=0)

    status_before = models.CharField(max_length=20, blank=True)
    status_after = models.CharField(max_length=20, blank=True)

    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    actor_role = models.CharField(max_length=80, blank=True)
    actor_username = models.CharField(max_length=150, blank=True)

    notes = models.CharField(max_length=255, blank=True)
    payload = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Action Audit Log"
        verbose_name_plural = "Action Audit Logs"
        permissions = [
            ("can_review_requests", "Can review and action blood requests"),
            ("can_review_donations", "Can review and action blood donations"),
            ("can_view_audit_logs", "Can view action audit logs"),
            ("can_export_reports", "Can export admin reports"),
        ]

    def __str__(self):
        return f"{self.action} {self.entity_type}#{self.entity_id} by {self.actor_username or 'system'}"


class ReportExportLog(models.Model):
    FORMAT_CSV = 'csv'
    FORMAT_PDF = 'pdf'

    STATUS_SUCCESS = 'SUCCESS'
    STATUS_FALLBACK = 'FALLBACK'
    STATUS_FAILED = 'FAILED'

    report_key = models.CharField(max_length=32)
    export_format = models.CharField(max_length=8, choices=[(FORMAT_CSV, 'CSV'), (FORMAT_PDF, 'PDF')])
    rows_exported = models.PositiveIntegerField(default=0)

    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    actor_username = models.CharField(max_length=150, blank=True)
    actor_role = models.CharField(max_length=80, blank=True)

    filters = models.JSONField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=[
            (STATUS_SUCCESS, 'Success'),
            (STATUS_FALLBACK, 'Fallback'),
            (STATUS_FAILED, 'Failed'),
        ],
        default=STATUS_SUCCESS,
    )
    error = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']
        verbose_name = 'Report Export Log'
        verbose_name_plural = 'Report Export Logs'

    def __str__(self):
        return f"{self.report_key} ({self.export_format}) by {self.actor_username or 'system'}"


class EmergencyBroadcast(models.Model):
    STATUS_PENDING = 'PENDING'
    STATUS_SENT = 'SENT'
    STATUS_PARTIAL = 'PARTIAL'
    STATUS_FAILED = 'FAILED'

    blood_request = models.ForeignKey(BloodRequest, on_delete=models.CASCADE, related_name='broadcasts')
    triggered_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    message = models.TextField(blank=True)
    status = models.CharField(
        max_length=16,
        choices=[
            (STATUS_PENDING, 'Pending'),
            (STATUS_SENT, 'Sent'),
            (STATUS_PARTIAL, 'Partially Sent'),
            (STATUS_FAILED, 'Failed'),
        ],
        default=STATUS_PENDING,
    )
    total_targets = models.PositiveIntegerField(default=0)
    total_sent = models.PositiveIntegerField(default=0)
    total_failed = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f"Broadcast #{self.id} for request #{self.blood_request_id}"


class BroadcastDelivery(models.Model):
    CHANNEL_SMS = 'SMS'
    CHANNEL_EMAIL = 'EMAIL'
    CHANNEL_INAPP = 'INAPP'

    STATUS_PENDING = 'PENDING'
    STATUS_SENT = 'SENT'
    STATUS_FAILED = 'FAILED'

    broadcast = models.ForeignKey(EmergencyBroadcast, on_delete=models.CASCADE, related_name='deliveries')
    donor = models.ForeignKey(dmodels.Donor, null=True, blank=True, on_delete=models.CASCADE)
    channel = models.CharField(max_length=12, choices=[
        (CHANNEL_SMS, 'SMS'),
        (CHANNEL_EMAIL, 'Email'),
        (CHANNEL_INAPP, 'In-App'),
    ])
    status = models.CharField(max_length=12, choices=[
        (STATUS_PENDING, 'Pending'),
        (STATUS_SENT, 'Sent'),
        (STATUS_FAILED, 'Failed'),
    ], default=STATUS_PENDING)
    destination = models.CharField(max_length=160, blank=True)
    detail = models.CharField(max_length=255, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']


class InAppNotification(models.Model):
    donor = models.ForeignKey(dmodels.Donor, null=True, blank=True, on_delete=models.CASCADE)
    patient = models.ForeignKey(pmodels.Patient, null=True, blank=True, on_delete=models.CASCADE)
    title = models.CharField(max_length=120)
    message = models.TextField()
    related_request = models.ForeignKey(BloodRequest, null=True, blank=True, on_delete=models.SET_NULL)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']


class DonationAppointmentSlot(models.Model):
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    capacity = models.PositiveIntegerField(default=10)
    is_active = models.BooleanField(default=True)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['start_at', 'id']

    def __str__(self):
        return f"Slot {self.start_at:%d %b %Y %H:%M}"


class DonationAppointment(models.Model):
    STATUS_PENDING = 'PENDING'
    STATUS_APPROVED = 'APPROVED'
    STATUS_RESCHEDULED = 'RESCHEDULED'
    STATUS_NO_SHOW = 'NO_SHOW'
    STATUS_COMPLETED = 'COMPLETED'
    STATUS_CANCELLED = 'CANCELLED'

    donor = models.ForeignKey(dmodels.Donor, on_delete=models.CASCADE)
    slot = models.ForeignKey(DonationAppointmentSlot, null=True, blank=True, on_delete=models.SET_NULL)
    requested_for = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=[
        (STATUS_PENDING, 'Pending'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_RESCHEDULED, 'Rescheduled'),
        (STATUS_NO_SHOW, 'No Show'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_CANCELLED, 'Cancelled'),
    ], default=STATUS_PENDING)
    notes = models.CharField(max_length=255, blank=True)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-requested_at', '-id']


class VerificationBadge(models.Model):
    badge_name = models.CharField(max_length=60, default='Verified Identity')
    donor = models.ForeignKey(dmodels.Donor, null=True, blank=True, on_delete=models.CASCADE)
    patient = models.ForeignKey(pmodels.Patient, null=True, blank=True, on_delete=models.CASCADE)
    hospital_name = models.CharField(max_length=120, blank=True)
    is_verified = models.BooleanField(default=False)
    trust_score = models.PositiveSmallIntegerField(default=50)
    verified_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    verified_at = models.DateTimeField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-verified_at', '-id']

    def __str__(self):
        owner = self.donor.get_name if self.donor_id else (self.patient.get_name if self.patient_id else 'Unknown')
        return f"{owner} • {self.badge_name}"


class PasswordResetOTP(models.Model):
    """Stores one-time passwords for SMS-based password reset."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='password_reset_otps')
    otp = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def is_valid(self):
        """OTP expires after 10 minutes."""
        from django.utils import timezone
        return (not self.is_used) and (timezone.now() - self.created_at).total_seconds() < 600

    def __str__(self):
        return f"OTP for {self.user.username} ({'used' if self.is_used else 'active'})"

