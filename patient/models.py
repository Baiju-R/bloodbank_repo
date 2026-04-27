from django.db import models
from django.contrib.auth.models import User
from django.core.validators import RegexValidator

AADHAAR_VALIDATOR = RegexValidator(
    regex=r'^\d{12}$',
    message='Aadhaar number must be exactly 12 digits.',
)


class Patient(models.Model):
    user=models.OneToOneField(User,on_delete=models.CASCADE)
    profile_pic= models.ImageField(upload_to='profile_pic/Patient/',null=True,blank=True)

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

    # Doctor prescription (mandatory for signup)
    doctor_prescription = models.FileField(
        upload_to='prescriptions/patient/%Y/%m/',
        null=True,
        blank=True,
        help_text='Upload doctor prescription (PDF, JPG, PNG, DOC, etc.)',
    )

    age=models.PositiveIntegerField()
    bloodgroup=models.CharField(max_length=10)
    disease=models.CharField(max_length=100)
    doctorname=models.CharField(max_length=50)

    address = models.CharField(max_length=40)
    mobile = models.CharField(max_length=20,null=False)
   
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